# Copyright 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys

from platformio.public import PlatformBase


IS_WINDOWS = sys.platform.startswith("win")


class SifivePlatform(PlatformBase):

    def configure_default_packages(self, variables, targets):
        if "zephyr" in variables.get("pioframework", []):
            for p in self.packages:
                if p in ("tool-cmake", "tool-dtc", "tool-ninja"):
                    self.packages[p]["optional"] = False
            if not IS_WINDOWS:
                self.packages["tool-gperf"]["optional"] = False
        if "arduino" in variables.get("pioframework", []):
            self.packages["framework-bl-iot-sdk-arduino"]["optional"] = False               

        upload_protocol = variables.get(
            "upload_protocol",
            self.board_config(variables.get("board")).get(
                "upload.protocol", ""))

        if upload_protocol == "renode" and "debug" not in targets:
            self.packages["tool-renode"]["type"] = "uploader"

        return super().configure_default_packages(variables, targets)

    def get_boards(self, id_=None):
        result = super().get_boards(id_)
        if not result:
            return result
        if id_:
            return self._add_default_debug_tools(result)
        else:
            for key in result:
                result[key] = self._add_default_debug_tools(result[key])
        return result

    def _add_default_debug_tools(self, board):
        debug = board.manifest.get("debug", {})
        upload_protocols = board.manifest.get("upload",
                                              {}).get("protocols", [])
        if "tools" not in debug:
            debug["tools"] = {}

        tools = ("jlink", "qemu", "renode", "ftdi", "minimodule",
                 "olimex-arm-usb-tiny-h", "olimex-arm-usb-ocd-h",
                 "olimex-arm-usb-ocd", "olimex-jtag-tiny", "tumpa")
        for tool in tools:
            if tool in ("qemu", "renode"):
                if not debug.get("%s_machine" % tool):
                    continue
            elif (tool not in upload_protocols or tool in debug["tools"]):
                continue
            if tool == "jlink":
                assert debug.get("jlink_device"), (
                    "Missed J-Link Device ID for %s" % board.id)
                debug["tools"][tool] = {
                    "server": {
                        "package": "tool-jlink",
                        "arguments": [
                            "-singlerun",
                            "-if", "JTAG",
                            "-select", "USB",
                            "-jtagconf", "-1,-1",
                            "-device", debug.get("jlink_device"),
                            "-port", "2331"
                        ],
                        "executable": ("JLinkGDBServerCL.exe"
                                       if IS_WINDOWS else
                                       "JLinkGDBServer")
                    },
                    "onboard": tool in debug.get("onboard_tools", [])
                }

            elif tool == "qemu":
                machine64bit = "64" in board.get("build.mabi")
                debug["tools"][tool] = {
                    "server": {
                        "package": "tool-qemu-riscv",
                        "arguments": [
                            "-nographic",
                            "-machine", debug.get("qemu_machine"),
                            "-d", "unimp,guest_errors",
                            "-gdb", "tcp::1234",
                            "-S"
                        ],
                        "executable": "bin/qemu-system-riscv%s" % (
                            "64" if machine64bit else "32")
                    }
                }
            elif tool == "renode":
                assert debug.get("renode_machine"), (
                    "Missing Renode machine ID for %s" % board.id)
                debug["tools"][tool] = {
                    "server": {
                        "package": "tool-renode",
                        "arguments": [
                            "--disable-xwt",
                            "-e", "include @%s" % os.path.join(
                                "scripts", "single-node", debug.get("renode_machine")),
                            "-e", "machine StartGdbServer 3333 True"
                        ],
                        "executable": ("bin/Renode"
                                       if IS_WINDOWS else
                                       "renode"),
                        "ready_pattern": "GDB server with all CPUs started on port"

                    }
                }
            else:
                server_args = [
                    "-s", "$PACKAGE_DIR/share/openocd/scripts"
                ]
                sdk_dir = self.get_package_dir("framework-freedom-e-sdk")
                board_cfg = os.path.join(
                    sdk_dir or "", "bsp", "sifive-%s" % board.id, "openocd.cfg")
                if os.path.isfile(board_cfg):
                    server_args.extend(["-f", board_cfg])
                elif board.id == "e310-arty":
                    server_args.extend([
                        "-f", os.path.join("interface", "ftdi", "%s.cfg" % (
                            "arty-onboard-ftdi" if tool == "ftdi" else tool)),
                        "-f", os.path.join(
                            sdk_dir or "", "bsp", "freedom-e310-arty", "openocd.cfg")
                    ])
                else:
                    assert "Unknown debug configuration", board.id
                debug["tools"][tool] = {
                    "server": {
                        "package": "tool-openocd-riscv",
                        "executable": "bin/openocd",
                        "arguments": server_args
                    },
                    "onboard": tool in debug.get("onboard_tools", []),
                    "init_cmds": debug.get("init_cmds", None)
                }

        board.manifest["debug"] = debug
        return board

    def configure_debug_session(self, debug_config):
        if debug_config.speed:
            server_executable = (debug_config.server or {}).get("executable", "").lower()
            if "openocd" in server_executable:
                debug_config.server["arguments"].extend(
                    ["-c", "adapter speed %s" % debug_config.speed]
                )
            elif "jlink" in server_executable:
                debug_config.server["arguments"].extend(
                    ["-speed", debug_config.speed]
                )
