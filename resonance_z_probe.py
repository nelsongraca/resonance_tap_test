"""
RESONANCE Z PROBE

Copyright (C) 2024  Francesco Favero

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.


"""

import os
import collections
import math
import numpy as np
from datetime import datetime

from matplotlib.backends.backend_pdf import PdfPages
from matplotlib import font_manager
import matplotlib.pyplot as plt
from textwrap import wrap


TestPoint = collections.namedtuple(
    "TestPoint",
    (
        "time",
        "accel_x",
        "accel_y",
        "accel_z",
        "current_z",
    ),
)


class ZVibrationHelper:
    """Helper to dynamically manage Z position and movement, including the vibration"""

    def __init__(self, printer, frequency, accel_per_hz) -> None:
        self.printer = printer
        self.gcode = self.printer.lookup_object("gcode")
        self.frequency = frequency
        self.accel_per_hz = accel_per_hz
        vib_dir = (0.0, 0.0, 1.0)
        s = math.sqrt(sum([d * d for d in vib_dir]))
        self._vib_dir = [d / s for d in vib_dir]
        self.input_shaper_was_on = False
        self.input_shaper = None

    def _set_vibration_variables(self):
        """Calculate the axis coordinate difference to perform the vibration movement"""
        t_seg = 0.25 / self.frequency
        accel = self.accel_per_hz * self.frequency
        self.max_v = accel * t_seg
        toolhead = self.printer.lookup_object("toolhead")
        self.cur_x, self.cur_y, self.cur_z, self.cur_e = toolhead.get_position()
        toolhead.cmd_M204(self.gcode.create_gcode_command("M204", "M204", {"S": accel}))
        self.movement_span = 0.5 * accel * t_seg**2
        self.dX, self.dY, self.dZ = self.get_point(self.movement_span)

    def get_point(self, l):
        return (self._vib_dir[0] * l, self._vib_dir[1] * l, self._vib_dir[2] * l)

    def _vibrate_(self):
        toolhead = self.printer.lookup_object("toolhead")
        for sign in [1, -1]:
            nX = self.cur_x + sign * self.dX
            nY = self.cur_y + sign * self.dY
            nZ = self.cur_z + sign * self.dZ
            toolhead.move([nX, nY, nZ, self.cur_e], self.max_v)
            toolhead.move([self.cur_x, self.cur_y, self.cur_z, self.cur_e], self.max_v)

    def disable_input_shaper(self):
        self.input_shaper = self.printer.lookup_object("input_shaper", None)
        if self.input_shaper is not None:
            self.input_shaper.disable_shaping()
            self.input_shaper_was_on = True

    def restore_input_shaper(self):
        if self.input_shaper_was_on:
            self.input_shaper.enable_shaping()

    def vibrate_n(self, n):
        self._set_vibration_variables()
        counter = 0
        while counter <= n:
            self._vibrate_()
            counter += 1


class TapResonanceData:
    def __init__(self, test_points, out_path, gcmd):
        self.data = test_points
        self.gcmd = gcmd
        self.ts = datetime.timestamp(datetime.now())
        self.pdf_out = os.path.join(out_path, "tap_summary_%s.pdf" % self.ts)
        self.csv_out = os.path.join(out_path, "tap_summary_%s.csv" % self.ts)

    def _n_test(self):
        return len(self.data)

    def _rate_above_threshold(self, threshold):
        rates = []
        z_height = []
        for t, x, y, z, curr_z in self.data:

            rate_above_tr = sum(
                np.logical_or(z > threshold, z < (-1 * threshold))
            ) / len(t)
            rates.append(rate_above_tr)
            z_height.append(curr_z)
        return (z_height, rates)

    def plot(self, threshold, cycles):
        rates_above_tr = self._rate_above_threshold(threshold)
        self.gcmd.respond_info("writing debug plots to %s" % self.pdf_out)

        with PdfPages(self.pdf_out) as pdf:

            plt.plot(
                rates_above_tr[0],
                rates_above_tr[1],
                linestyle="-",
                marker="o",
            )
            fontP = font_manager.FontProperties()
            fontP.set_size("x-small")
            plt.xlabel("Z height")
            plt.ylabel("Rate of points above threshold")
            pdf.savefig(facecolor="white")
            plt.close()
            for z_test in rates_above_tr[0]:
                acc_plot = self.plot_accel(z_test, cycles, threshold)
                pdf.savefig(acc_plot, facecolor="white")
                plt.close()

    def write_data(self):
        self.gcmd.respond_info("writing data to %s" % self.csv_out)
        with open(self.csv_out, "wt") as data_out:
            data_out.write("#time,accel_x,accel_y,accel_z,z_height\n")
            for t, x, y, z, curr_z in self.data:
                for i in range(len(t)):
                    data_out.write(
                        "%.6f,%.6f,%.6f,%.6f,%.6f\n" % (t[i], x[i], y[i], z[i], curr_z)
                    )

    def plot_accel(self, z_height, cycles, threshold):
        data = None
        for t, x, y, z, curr_z in self.data:
            if curr_z == z_height:
                data = np.array((t, x, y, z))
        if data is None:
            self.gcmd.respond_info("No corresponding z_height found")
            return None
        logname = "%.4f" % z_height
        fig, axes = plt.subplots(nrows=3, sharex=True)
        axes[0].set_title("\n".join(wrap("Accelerometer data z=%s" % logname, 15)))
        axis_names = ["Expected taps", "z-accel", "both"]
        first_time = data[0, 0]
        times = data[0, :] - first_time
        time_span = times[-1]
        expect_freq = cycles / time_span
        prob_wave = np.sin(2 * np.pi * expect_freq * times)
        prob_wave[prob_wave < 0] = 0
        ax = axes[0]
        ax.plot(times, prob_wave, alpha=0.8, label="Expected taps")
        # times = data[:, 0]
        adata = data[3, :]
        ax = axes[1]
        label = "\n".join(wrap(logname, 60))
        ax.plot(times, adata, alpha=0.8, label="z")
        ax.axhline(y=threshold, linestyle="--", lw=2, label="threshold", color="red")
        ax.axhline(y=-1 * threshold, linestyle="--", lw=2, color="red")
        ax = axes[2]
        ax.plot(times, adata * prob_wave, alpha=0.8, label="normalized")
        ax.axhline(y=threshold, linestyle="--", lw=2, label="threshold", color="red")
        ax.axhline(y=-1 * threshold, linestyle="--", lw=2, color="red")
        axes[-1].set_xlabel("Time (s)")
        fontP = font_manager.FontProperties()
        fontP.set_size("x-small")
        for i in range(len(axis_names)):
            ax = axes[i]
            ax.grid(True)
            ax.legend(loc="best", prop=fontP)
            ax.set_ylabel("%s" % (axis_names[i],))
        fig.tight_layout()
        return fig


class OffsetHelper:
    """
    Wraps the decision making into the next z position to test, and mark when
    a z offset was detected successfully
    """

    def __init__(self, pos, step, min_precision=0.005) -> None:

        self.last_tested_pos = None
        self.step = step
        self.last_tested_pos = (pos, False)
        self.min_precision = min_precision
        self.current_offset = None
        self.finished = False
        self.started = False

    def next_position(self):
        if self.finished:
            return self.current_offset
        else:
            if self.started is False:
                return self.last_tested_pos[0]
            else:
                pos, status = self.last_tested_pos
                if status == True:
                    self.current_offset = pos
                    if self.step <= self.min_precision:
                        self.finished = True
                    return pos + self.step
                else:
                    return pos - self.step

    def last_tested_position(self, position, triggered):
        self.last_tested_pos = (position, triggered)
        self.started = True
        if triggered:
            self.step = self.step / 2.0


class ResonanceZProbe:
    def __init__(self, config):
        self.config = config
        self.printer = self.config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        # consider that accel_per_hz * freq might be caped to the printer max accel
        self.accel_per_hz = self.config.getfloat("accel_per_hz", 1.5, above=0.0)

        self.step_size = self.config.getfloat("step_size", 0.01, minval=0.005)
        self.tolerance = config.getfloat("samples_tolerance", None, above=0.0)
        self.z_freq = self.config.getfloat(
            "z_vibration_freq", 80, minval=50.0, maxval=200.0
        )
        self.amp_threshold = self.config.getfloat(
            "amplitude_threshold", 700.0, above=500.0
        )
        self.rate_above_threshold = self.config.getfloat(
            "rate_above_threshold", 0.015, minval=0.0, maxval=1.0
        )
        self.safe_min_z = self.config.getfloat("safe_min_z", 1)
        self.probe_points = self.config.getfloatlist("probe_points", sep=",", count=3)

        self.cycle_per_test = self.config.getint(
            "cycle_per_test", 50, minval=2, maxval=500
        )
        self.accel_chip_name = self.config.get("accel_chip").strip()
        self.gcode.register_command(
            "CALIBRATE_Z_RESONANCE",
            self.cmd_CALIBRATE_Z_RESONANCE,
            desc=self.cmd_CALIBRATE_Z_RESONANCE_help,
        )
        self.printer.register_event_handler("klippy:connect", self.connect)
        self.vibration_helper = ZVibrationHelper(
            self.printer, self.z_freq, self.accel_per_hz
        )
        self.vibration_helper.disable_input_shaper()
        self.debug = True
        self.data_points = []

    def connect(self):
        self.accel_chips = ("z", self.printer.lookup_object(self.accel_chip_name))

    cmd_CALIBRATE_Z_RESONANCE_help = "Calibrate Z making the bed vibrate while probing with the nozzle and record accelerometer data"

    def cmd_CALIBRATE_Z_RESONANCE(self, gcmd):
        self.babystep_probe(gcmd)

    def babystep_probe(self, gcmd):
        """
        move to the test position, start recording acc while resonate z, measure the amplitude and compare with the amp_threshold.
        lower by babystep until min safe Z is reached or the amp_threshold is passed.
        log stuff in the console
        """
        # move thself.probe_pointe toolhead
        test_results = []
        self.offset_helper = OffsetHelper(
            self.probe_points[2], self.step_size, self.tolerance
        )
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.manual_move(self.probe_points, 50.0)
        toolhead.wait_moves()
        toolhead.dwell(0.500)
        self.vibration_helper._set_vibration_variables()
        curr_z = self.probe_points[2]
        while curr_z >= self.safe_min_z:
            if self.offset_helper.finished:
                break
            results = self._test(gcmd, curr_z)
            self.offset_helper.last_tested_position(
                curr_z, results >= self.rate_above_threshold
            )
            test_results.append((self.probe_points[2], results))
            self.vibration_helper.cur_z = self.offset_helper.next_position()
            curr_z = self.vibration_helper.cur_z

        # for res in test_results:
        #     gcmd.respond_info("Z:%.4f percentage outside threshold %.2f%%" % res)
        if self.offset_helper.finished:
            gcmd.respond_info(
                "probe at %.4f,%.4f  is z=%.6f"
                % (
                    self.probe_points[0],
                    self.probe_points[1],
                    self.offset_helper.current_offset,
                )
            )

        if self.debug:
            tap_data = TapResonanceData(self.data_points, "/tmp", gcmd)
            tap_data.write_data()
            tap_data.plot(self.amp_threshold, self.cycle_per_test)

    def _test(self, gcmd, curr_z):

        chip = self.accel_chips[1]
        aclient = chip.start_internal_client()
        aclient.msg = []
        aclient.samples = []
        cycle_counter = 0
        while cycle_counter <= self.cycle_per_test:
            self.vibration_helper._vibrate_()
            cycle_counter += 1
        timestamps = []
        x_data = []
        y_data = []
        z_data = []
        aclient.finish_measurements()
        for t, accel_x, accel_y, accel_z in aclient.get_samples():
            timestamps.append(t)
            x_data.append(accel_x)
            y_data.append(accel_y)
            z_data.append(accel_z)

        x = np.asarray(x_data)
        y = np.asarray(y_data)
        z = np.asarray(z_data)
        x = x - np.median(x)
        y = y - np.median(y)
        z = z - np.median(z)
        try:
            rate_above_tr = sum(
                np.logical_or(z > self.amp_threshold, z < (-1 * self.amp_threshold))
            ) / len(timestamps)
        except ZeroDivisionError:
            rate_above_tr = 0

        if self.debug:
            if len(timestamps) > 0:
                test_time = timestamps[len(timestamps) - 1] - timestamps[0]
            else:
                test_time = 0
            gcmd.respond_info(
                "Testing Z: %.4f. Received %i samples in %.2f seconds. Percentage above threshold: %.1f%%"
                % (curr_z, len(timestamps), test_time, 100 * rate_above_tr)
            )
            self.data_points.append(TestPoint(timestamps, x, y, z, curr_z))
        return rate_above_tr


def load_config(config):
    return ResonanceZProbe(config)
