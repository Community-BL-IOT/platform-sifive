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

import sys
from platform import system
from os import makedirs
from os.path import isdir, join
import semantic_version
import subprocess
import json

from SCons.Script import (ARGUMENTS, COMMAND_LINE_TARGETS, AlwaysBuild,
                          Builder, Default, DefaultEnvironment)

from platformio.util import get_serial_ports
from platformio.package.version import get_original_version, pepver_to_semver

def BeforeUpload(target, source, env):  # pylint: disable=W0613,W0621
    env.AutodetectUploadPort()

    upload_options = {}
    if "BOARD" in env:
        upload_options = env.BoardConfig().get("upload", {})

    if not bool(upload_options.get("disable_flushing", False)):
        env.FlushSerialBuffer("$UPLOAD_PORT")

    before_ports = get_serial_ports()

    if bool(upload_options.get("use_1200bps_touch", False)):
        env.TouchSerialPort("$UPLOAD_PORT", 1200)

    if bool(upload_options.get("wait_for_upload_port", False)):
        env.Replace(UPLOAD_PORT=env.WaitForNewSerialPort(before_ports))

def install_python_deps():
    def _get_installed_pip_packages():
        result = {}
        packages = {}
        pip_output = subprocess.check_output(
            [env.subst("$PYTHONEXE"), "-m", "pip", "list", "--format=json"]
        )
        try:
            packages = json.loads(pip_output)
        except:
            print("Warning! Couldn't extract the list of installed Python packages.")
            return {}
        for p in packages:
            result[p["name"]] = pepver_to_semver(p["version"])

        return result

    deps = {
        "tqdm": ">=4.62.2",
    }

    installed_packages = _get_installed_pip_packages()
    packages_to_install = []
    for package, spec in deps.items():
        if package not in installed_packages:
            packages_to_install.append(package)
        else:
            version_spec = semantic_version.Spec(spec)
            if not version_spec.match(installed_packages[package]):
                packages_to_install.append(package)

    if packages_to_install:
        env.Execute(
            env.VerboseAction(
                (
                    '"$PYTHONEXE" -m pip install -U --force-reinstall '
                    + " ".join(['"%s%s"' % (p, deps[p]) for p in packages_to_install])
                ),
                "Installing BL60X-Flash's Python dependencies",
            )
        )

env = DefaultEnvironment()
platform = env.PioPlatform()
board_config = env.BoardConfig()

env.Replace(
    AR="riscv64-unknown-elf-gcc-ar",
    AS="riscv64-unknown-elf-as",
    CC="riscv64-unknown-elf-gcc",
    GDB="riscv64-unknown-elf-gdb",
    CXX="riscv64-unknown-elf-g++",
    OBJCOPY="riscv64-unknown-elf-objcopy",
    RANLIB="riscv64-unknown-elf-gcc-ranlib",
    SIZETOOL="riscv64-unknown-elf-size",

    ARFLAGS=["rc"],

    SIZEPRINTCMD='$SIZETOOL -d $SOURCES',

    PROGSUFFIX=".elf"
)

# Allow user to override via pre:script
if env.get("PROGNAME", "program") == "program":
    env.Replace(PROGNAME="firmware")

env.Append(
    BUILDERS=dict(
        ElfToHex=Builder(
            action=env.VerboseAction(" ".join([
                "$OBJCOPY",
                "-O",
                "ihex",
                "$SOURCES",
                "$TARGET"
            ]), "Building $TARGET"),
            suffix=".hex"
        ),
        ElfToBin=Builder(
            action=env.VerboseAction(" ".join([
                "$OBJCOPY",
                "-S",
                "-O",
                "binary",
                "$SOURCES",
                "$TARGET"
            ]), "Building $TARGET"),
            suffix=".bin"
        )
    )
)

pioframework = env.get("PIOFRAMEWORK", [])

if not pioframework:
    env.SConscript("frameworks/_bare.py", exports="env")

#
# Target: Build executable and linkable firmware
#

if "zephyr" in pioframework:
    env.SConscript(
        join(platform.get_package_dir(
            "framework-zephyr"), "scripts", "platformio", "platformio-build-pre.py"),
        exports={"env": env}
    )

target_elf = None
if "nobuild" in COMMAND_LINE_TARGETS:
    target_elf = join("$BUILD_DIR", "${PROGNAME}.elf")
    target_hex = join("$BUILD_DIR", "${PROGNAME}.hex")
    target_bin = join("$BUILD_DIR", "${PROGNAME}.bin")
else:
    target_elf = env.BuildProgram()
    target_hex = env.ElfToHex(join("$BUILD_DIR", "${PROGNAME}"), target_elf)
    target_bin = env.ElfToBin(join("$BUILD_DIR", "${PROGNAME}"), target_elf)
    env.Depends(target_hex, "checkprogsize")

AlwaysBuild(env.Alias("nobuild", [target_hex, target_bin]))
target_buildprog = env.Alias("buildprog", [target_hex, target_bin], target_hex)

#
# Target: Print binary size
#

target_size = env.Alias(
    "size", target_elf,
    env.VerboseAction("$SIZEPRINTCMD", "Calculating size $SOURCE"))
AlwaysBuild(target_size)

#
# Target: Upload by default .bin file
#

upload_protocol = env.subst("$UPLOAD_PROTOCOL")
debug_tools = board_config.get("debug.tools", {})
upload_actions = []
upload_target = target_elf

if upload_protocol.startswith("jlink"):

    def _jlink_cmd_script(env, source):
        build_dir = env.subst("$BUILD_DIR")
        if not isdir(build_dir):
            makedirs(build_dir)
        script_path = join(build_dir, "upload.jlink")
        commands = [
            "h",
            "loadfile %s" % source,
            "r",
            "q"
        ]
        with open(script_path, "w") as fp:
            fp.write("\n".join(commands))
        return script_path

    env.Replace(
        __jlink_cmd_script=_jlink_cmd_script,
        UPLOADER="JLink.exe" if system() == "Windows" else "JLinkExe",
        UPLOADERFLAGS=[
            "-device", env.BoardConfig().get("debug", {}).get("jlink_device"),
            "-speed", env.GetProjectOption("debug_speed", "4000"),
            "-if", "JTAG",
            "-jtagconf", "-1,-1",
            "-autoconnect", "1",
            "-NoGui", "1"
        ],
        UPLOADCMD='$UPLOADER $UPLOADERFLAGS -CommanderScript "${__jlink_cmd_script(__env__, SOURCE)}"'
    )
    upload_target = target_hex
    upload_actions = [env.VerboseAction("$UPLOADCMD", "Uploading $SOURCE")]

elif upload_protocol in debug_tools:
    if upload_protocol == "renode":
        uploader = "renode"
        tool_args = [arg for arg in debug_tools.get(upload_protocol).get(
            "server").get("arguments", []) if arg != "--disable-xwt"]
        tool_args.extend([
            "-e", "sysbus LoadELF @$SOURCE",
            "-e", "start"
        ])
    else:
        uploader = "openocd"
        tool_args = [
            "-c",
            "debug_level %d" % (2 if int(ARGUMENTS.get("PIOVERBOSE", 0)) else 1),
            "-s", platform.get_package_dir("tool-openocd-riscv") or ""
        ]
        tool_args.extend(
            debug_tools.get(upload_protocol).get("server").get("arguments", []))
        if env.GetProjectOption("debug_speed"):
            tool_args.extend(
                ["-c", "adapter_khz %s" % env.GetProjectOption("debug_speed")]
            )
        tool_args.extend([
            "-c", "program {$SOURCE} %s verify; shutdown;" %
            board_config.get("upload").get("flash_start", "")
        ])
    env.Replace(
        UPLOADER=uploader,
        UPLOADERFLAGS=tool_args,
        UPLOADCMD="$UPLOADER $UPLOADERFLAGS"
    )
    upload_actions = [env.VerboseAction("$UPLOADCMD", "Uploading $SOURCE")]
elif upload_protocol == "bl60x-flash":
    # make sure tqdm is installed for python flasher script
    install_python_deps()
    env.Replace(
        UPLOADER= '"%s"' % join(platform.get_package_dir("tool-bl60x-flash") or "", "bl602-flasher.py"),
        UPLOADERFLAGS=[
            # no flags needed 
        ],
        UPLOADCMD='"$PYTHONEXE" $UPLOADER $UPLOADERFLAGS "$UPLOAD_PORT" "$SOURCE"'
    )
    upload_actions = [
        env.VerboseAction(env.AutodetectUploadPort, "Looking for upload port..."),
        env.VerboseAction("$UPLOADCMD", "Uploading $SOURCE")
    ]
    upload_target = target_bin
# custom upload tool
elif upload_protocol == "custom":
    upload_actions = [env.VerboseAction("$UPLOADCMD", "Uploading $SOURCE")]

else:
    sys.stderr.write("Warning! Unknown upload protocol %s\n" % upload_protocol)

AlwaysBuild(env.Alias("upload", upload_target, upload_actions))


#
# Setup default targets
#

Default([target_buildprog, target_size])
