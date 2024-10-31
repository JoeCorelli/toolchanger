# KTCC - Klipper Tool Changer Code
# Toollock and general Tool support
#
# Copyright (C) 2023  Andrei Ignat <andrei@ignat.se>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# To try to keep terms apart:
# Mount: Tool is selected and loaded for use, be it a physical or a virtual on physical.
# Unmount: Tool is unselected and unloaded, be it a physical or a virtual on physical.
# Pickup: Tool is physically picked up and attached to the toolchanger head.
# Dropoff: Tool is physically parked and dropped off the toolchanger head.
# ToolLock: Tool lock is engaged.
# ToolUnlock: Tool lock is disengaged.

class ToolLock:
    TOOL_UNKNOWN = -2
    TOOL_UNLOCKED = -1
    BOOT_DELAY = 1.5
    VARS_KTCC_TOOL_MAP = "ktcc_state_tool_remap"

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        gcode_macro = self.printer.load_object(config, 'gcode_macro')

        self.global_offset = config.get('global_offset', "0,0,0")
        if isinstance(self.global_offset, str):
            offset_list = self.global_offset.split(',')
            if len(offset_list) == 3 and all(x.replace('.', '').isdigit() for x in offset_list):
                self.global_offset = [float(x) for x in offset_list]
            else:
                raise ValueError("global_offset must contain 3 float numbers separated by commas")
        else:
            raise TypeError("global_offset must be a string")

        self.saved_fan_speed = 0
        self.tool_current = "-2"
        self.init_printer_to_last_tool = config.getboolean('init_printer_to_last_tool', True)
        self.purge_on_toolchange = config.getboolean('purge_on_toolchange', True)
        self.saved_position = None
        self.restore_axis_on_toolchange = ''
        self.log = self.printer.load_object(config, 'ktcclog')

        self.tool_map = {}
        self.last_endstop_query = {}
        self.changes_made_by_set_all_tool_heaters_off = {}

        self.tool_lock_gcode_template = gcode_macro.load_template(config, 'tool_lock_gcode', '')
        self.tool_unlock_gcode_template = gcode_macro.load_template(config, 'tool_unlock_gcode', '')

        handlers = [
            'SAVE_CURRENT_TOOL', 'TOOL_LOCK', 'TOOL_UNLOCK',
            'KTCC_TOOL_DROPOFF_ALL', 'SET_AND_SAVE_FAN_SPEED', 'TEMPERATURE_WAIT_WITH_TOLERANCE',
            'SET_TOOL_TEMPERATURE', 'SET_GLOBAL_OFFSET', 'SET_TOOL_OFFSET',
            'SET_PURGE_ON_TOOLCHANGE', 'SAVE_POSITION', 'SAVE_CURRENT_POSITION',
            'RESTORE_POSITION', 'KTCC_SET_GCODE_OFFSET_FOR_CURRENT_TOOL',
            'KTCC_DISPLAY_TOOL_MAP', 'KTCC_REMAP_TOOL', 'KTCC_ENDSTOP_QUERY',
            'KTCC_SET_ALL_TOOL_HEATERS_OFF', 'KTCC_RESUME_ALL_TOOL_HEATERS'
        ]
        for cmd in handlers:
            func = getattr(self, 'cmd_' + cmd)
            desc = getattr(self, 'cmd_' + cmd + '_help', None)
            self.gcode.register_command(cmd, func, False, desc)

        self.printer.register_event_handler("klippy:ready", self.handle_ready)

    def handle_ready(self):
        self.tool_map = self.printer.lookup_object('save_variables').allVariables.get(self.VARS_KTCC_TOOL_MAP, {})
        waketime = self.reactor.monotonic() + self.BOOT_DELAY
        self.reactor.register_callback(self._bootup_tasks, waketime)

    def _bootup_tasks(self, eventtime):
        try:
            if self.tool_map:
                self.log.always(self._tool_map_to_human_string())
            self.Initialize_Tool_Lock()
        except Exception as e:
            self.log.always(f'Warning: Error booting up KTCC: {e}')

    def Initialize_Tool_Lock(self):
        if not self.init_printer_to_last_tool:
            return

        save_variables = self.printer.lookup_object('save_variables')
        try:
            self.tool_current = save_variables.allVariables["tool_current"]
        except:
            self.tool_current = "-1"
            save_variables.cmd_SAVE_VARIABLE(
                self.gcode.create_gcode_command("SAVE_VARIABLE", "SAVE_VARIABLE", {"VARIABLE": "tool_current", 'VALUE': self.tool_current })
            )

        if str(self.tool_current) == "-1":
            self.cmd_TOOL_UNLOCK()
            self.log.always("ToolLock initialized unlocked")
        else:
            t = self.tool_current
            self.ToolLock(True)
            self.SaveCurrentTool(str(t))
            self.log.always(f"ToolLock initialized with T{self.tool_current}.")

    cmd_TOOL_LOCK_help = "Lock the ToolLock."
    def cmd_TOOL_LOCK(self, gcmd=None):
        self.ToolLock()

    def ToolLock(self, ignore_locked=False):
        self.log.trace("TOOL_LOCK running.")
        if not ignore_locked and int(self.tool_current) != -1:
            self.log.always(f"TOOL_LOCK is already locked with tool {self.tool_current}.")
        else:
            self.tool_lock_gcode_template.run_gcode_from_command()
            self.SaveCurrentTool("-2")
            self.log.trace("Tool Locked")
            self.log.increase_statistics('total_toollocks')

    cmd_TOOL_UNLOCK_help = "Unlock the ToolLock."
    def cmd_TOOL_UNLOCK(self, gcmd=None):
        self.log.trace("TOOL_UNLOCK running.")
        self.tool_unlock_gcode_template.run_gcode_from_command()
        self.SaveCurrentTool(-1)
        self.log.trace("ToolLock Unlocked.")
        self.log.increase_statistics('total_toolunlocks')

    def PrinterIsHomedForToolchange(self, lazy_home_when_parking=0):
        curtime = self.printer.get_reactor().monotonic()
        toolhead = self.printer.lookup_object('toolhead')
        homed = toolhead.get_status(curtime)['homed_axes'].lower()
        if all(axis in homed for axis in ['x', 'y', 'z']):
            return True
        elif lazy_home_when_parking == 0 and not all(axis in homed for axis in ['x', 'y', 'z']):
            return False
        elif lazy_home_when_parking == 1 and 'z' not in homed:
            return False

        axes_to_home = "".join(axis for axis in 'xyz' if axis not in homed)
        self.gcode.run_script_from_command("G28 " + axes_to_home.upper())
        return True

    def SaveCurrentTool(self, t):
        self.tool_current = str(t)
        save_variables = self.printer.lookup_object('save_variables')
        save_variables.cmd_SAVE_VARIABLE(
            self.gcode.create_gcode_command("SAVE_VARIABLE", "SAVE_VARIABLE", {"VARIABLE": "tool_current", 'VALUE': t})
        )

    cmd_SAVE_CURRENT_TOOL_help = "Save the current tool to file to load at printer startup."
    def cmd_SAVE_CURRENT_TOOL(self, gcmd):
        t = gcmd.get_int('T', None, minval=-2)
        if t is not None:
            self.SaveCurrentTool(t)

    cmd_SET_AND_SAVE_FAN_SPEED_help = "Save the fan speed to be recovered at ToolChange."
    def cmd_SET_AND_SAVE_FAN_SPEED(self, gcmd):
        fanspeed = gcmd.get_float('S', 1, minval=0, maxval=255)
        tool_id = gcmd.get_int('P', int(self.tool_current), minval=0)

        if tool_id < 0:
            self.log.always(f"cmd_SET_AND_SAVE_FAN_SPEED: Invalid tool: {tool_id}")
            return None

        if fanspeed > 1:
            fanspeed = fanspeed / 255.0

        self.SetAndSaveFanSpeed(tool_id, fanspeed)

    def SetAndSaveFanSpeed(self, tool_id, fanspeed):
        tool_is_remaped = self.tool_is_remaped(int(tool_id))
        if tool_is_remaped > -1:
            tool_id = tool_is_remaped

        tool = self.printer.lookup_object("tool " + str(tool_id))

        if tool.fan is None:
            self.log.debug(f"ToolLock.SetAndSaveFanSpeed: Tool {tool_id} has no fan.")
        else:
            self.SaveFanSpeed(fanspeed)
            self.gcode.run_script_from_command(f"SET_FAN_SPEED FAN={tool.fan} SPEED={fanspeed}")

    def SaveFanSpeed(self, fanspeed):
        self.saved_fan_speed = float(fanspeed)

    cmd_TEMPERATURE_WAIT_WITH_TOLERANCE_help = "Waits for current tool temperature, or a specified (TOOL) tool or (HEATER) heater's temperature within (TOLERANCE) tolerance."
    def cmd_TEMPERATURE_WAIT_WITH_TOLERANCE(self, gcmd):
        curtime = self.printer.get_reactor().monotonic()
        heater_name = None
        tool_id = gcmd.get_int('TOOL', None, minval=0)
        heater_id = gcmd.get_int('HEATER', None, minval=0)
        tolerance = gcmd.get_int('TOLERANCE', 1, minval=0)
        # Temperature wait for specified heater or tool with tolerance check
        if tool_id is not None and heater_id is not None:
            self.log.always("cmd_TEMPERATURE_WAIT_WITH_TOLERANCE: Can't use both TOOL and HEATER parameters.")
            return None
        elif tool_id is None and heater_id is None:
            tool_id = self.tool_current
            if int(self.tool_current) >= 0:
                heater_name = self.printer.lookup_object("tool " + self.tool_current).get_status()["extruder"]
            # Wait for bed
            self._Temperature_wait_with_tolerance(curtime, "heater_bed", tolerance)
        else:
            if tool_id is not None:
                tool_is_remaped = self.tool_is_remaped(int(tool_id))
                if tool_is_remaped > -1:
                    tool_id = tool_is_remaped
                heater_name = self.printer.lookup_object("tool " + str(tool_id)).get_status(curtime)["extruder"]
            elif heater_id == 0:
                heater_name = "heater_bed"
            elif heater_id == 1:
                heater_name = "extruder"
            else:
                heater_name = "extruder" + str(heater_id - 1)
        if heater_name is not None:
            self._Temperature_wait_with_tolerance(curtime, heater_name, tolerance)

    def _Temperature_wait_with_tolerance(self, curtime, heater_name, tolerance):
        target_temp = int(self.printer.lookup_object(heater_name).get_status(curtime)["target"])
        if target_temp > 40:
            self.log.always(f"Waiting for heater {heater_name} to reach {target_temp} ±{tolerance}°C.")
            self.gcode.run_script_from_command(
                f"TEMPERATURE_WAIT SENSOR={heater_name} MINIMUM={target_temp - tolerance} MAXIMUM={target_temp + tolerance}"
            )
            self.log.always(f"Wait for heater {heater_name} complete.")

    def _get_tool_id_from_gcmd(self, gcmd):
        tool_id = gcmd.get_int('TOOL', None, minval=0)
        if tool_id is None:
            tool_id = self.tool_current
        if int(tool_id) <= self.TOOL_UNLOCKED:
            self.log.always(f"_get_tool_id_from_gcmd: Tool {tool_id} is not valid.")
            return None
        else:
            tool_is_remaped = self.tool_is_remaped(int(tool_id))
            if tool_is_remaped > self.TOOL_UNLOCKED:
                tool_id = tool_is_remaped
        return tool_id

    cmd_SET_TOOL_TEMPERATURE_help = "Set temperature parameters for a specified tool."
    def cmd_SET_TOOL_TEMPERATURE(self, gcmd):
        tool_id = self._get_tool_id_from_gcmd(gcmd)
        if tool_id is None:
            return

        stdb_tmp = gcmd.get_float('STDB_TMP', None, minval=0)
        actv_tmp = gcmd.get_float('ACTV_TMP', None, minval=0)
        chng_state = gcmd.get_int('CHNG_STATE', None, minval=0, maxval=2)
        stdb_timeout = gcmd.get_float('STDB_TIMEOUT', None, minval=0)
        shtdwn_timeout = gcmd.get_float('SHTDWN_TIMEOUT', None, minval=0)

        tool = self.printer.lookup_object("tool " + str(tool_id))
        set_heater_cmd = {}
        if stdb_tmp is not None:
            set_heater_cmd["heater_standby_temp"] = int(stdb_tmp)
        if actv_tmp is not None:
            set_heater_cmd["heater_active_temp"] = int(actv_tmp)
        if stdb_timeout is not None:
            set_heater_cmd["idle_to_standby_time"] = stdb_timeout
        if shtdwn_timeout is not None:
            set_heater_cmd["idle_to_powerdown_time"] = shtdwn_timeout
        if chng_state is not None:
            set_heater_cmd["heater_state"] = chng_state
        if set_heater_cmd:
            tool.set_heater(**set_heater_cmd)
        else:
            self.log.trace("No temperature changes provided, displaying current settings.")
            msg = f"T{tool_id} Current Temperature Settings\n"
            msg += f" Active temperature: {tool.heater_active_temp}°C, Active to Standby timer: {tool.idle_to_standby_time} seconds\n"
            msg += f" Standby temperature: {tool.heater_standby_temp}°C, Standby to Off timer: {tool.idle_to_powerdown_time} seconds"
            gcmd.respond_info(msg)

    cmd_KTCC_SET_ALL_TOOL_HEATERS_OFF_help = "Turns off all heaters and saves changes to resume."
    def cmd_KTCC_SET_ALL_TOOL_HEATERS_OFF(self, gcmd):
        self.set_all_tool_heaters_off()

    def set_all_tool_heaters_off(self):
        all_tools = dict(self.printer.lookup_objects('tool'))
        self.changes_made_by_set_all_tool_heaters_off = {}

        try:
            for tool_name, tool in all_tools.items():
                if tool.get_status()["extruder"] is None:
                    continue
                if tool.get_status()["heater_state"] == 0:
                    continue
                self.log.trace(f"set_all_tool_heaters_off: T{tool_name} saved with heater_state: {tool.get_status()['heater_state']}.")
                self.changes_made_by_set_all_tool_heaters_off[tool_name] = tool.get_status()["heater_state"]
                tool.set_heater(heater_state=0)
        except Exception as e:
            raise Exception(f'set_all_tool_heaters_off: Error: {e}')

    cmd_KTCC_RESUME_ALL_TOOL_HEATERS_help = "Resumes heaters previously turned off by KTCC_SET_ALL_TOOL_HEATERS_OFF."
    def cmd_KTCC_RESUME_ALL_TOOL_HEATERS(self, gcmd):
        self.resume_all_tool_heaters()

    def resume_all_tool_heaters(self):
        try:
            for tool_name, state in self.changes_made_by_set_all_tool_heaters_off.items():
                if state == 1:
                    self.printer.lookup_object(str(tool_name)).set_heater(heater_state=state)
            for tool_name, state in self.changes_made_by_set_all_tool_heaters_off.items():
                if state == 2:
                    self.printer.lookup_object(str(tool_name)).set_heater(heater_state=state)
        except Exception as e:
            raise Exception(f'resume_all_tool_heaters: Error: {e}')

    cmd_SET_TOOL_OFFSET_help = "Set an individual tool offset."
    def cmd_SET_TOOL_OFFSET(self, gcmd):
        tool_id = self._get_tool_id_from_gcmd(gcmd)
        if tool_id is None:
            return

        offset_cmd = {k: gcmd.get_float(k) for k in ('X', 'X_ADJUST', 'Y', 'Y_ADJUST', 'Z', 'Z_ADJUST') if gcmd.get_float(k) is not None}
        if offset_cmd:
            tool = self.printer.lookup_object("tool " + str(tool_id))
            tool.set_offset(**offset_cmd)

    cmd_SET_GLOBAL_OFFSET_help = "Set the global tool offset."
    def cmd_SET_GLOBAL_OFFSET(self, gcmd):
        self.global_offset = [gcmd.get_float(axis, self.global_offset[i]) for i, axis in enumerate(['X', 'Y', 'Z'])]
        self.log.trace(f"Global offset now set to: {self.global_offset}")

    cmd_SET_PURGE_ON_TOOLCHANGE_help = "Set the purge status for the tool."
    def cmd_SET_PURGE_ON_TOOLCHANGE(self, gcmd=None):
        self.purge_on_toolchange = gcmd.get('VALUE', 'FALSE').upper() not in ('FALSE', '0')

    def SaveFanSpeed(self, fanspeed):
        self.saved_fan_speed = float(fanspeed)

    cmd_SAVE_POSITION_help = "Save the specified G-Code position."
    def cmd_SAVE_POSITION(self, gcmd):
        self.SavePosition(gcmd.get_float('X'), gcmd.get_float('Y'), gcmd.get_float('Z'))

    def SavePosition(self, param_X=None, param_Y=None, param_Z=None):
        self.saved_position = [param_X, param_Y, param_Z]
        self.restore_axis_on_toolchange = ''.join(axis for axis, param in zip('XYZ', [param_X, param_Y, param_Z]) if param is not None)

    cmd_SAVE_CURRENT_POSITION_help = "Save the current G-Code position."
    def cmd_SAVE_CURRENT_POSITION(self, gcmd):
        self.restore_axis_on_toolchange = parse_restore_type(gcmd, 'RESTORE_POSITION_TYPE')
        self.saved_position = self.printer.lookup_object('gcode_move')._get_gcode_position()

    cmd_RESTORE_POSITION_help = "Restore a previously saved G-Code position."
    def cmd_RESTORE_POSITION(self, gcmd):
        self.restore_axis_on_toolchange = parse_restore_type(gcmd, 'RESTORE_POSITION_TYPE', default=self.restore_axis_on_toolchange)
        speed = gcmd.get_int('F', None)
        if self.restore_axis_on_toolchange and self.saved_position is not None:
            cmd = 'G1 ' + ' '.join(f'{t}{self.saved_position[XYZ_TO_INDEX[t]]:.3f}' for t in self.restore_axis_on_toolchange)
            if speed:
                cmd += f" F{speed}"
            self.gcode.run_script_from_command(cmd)
            
    def get_status(self, eventtime=None):
        status = {
            "global_offset": self.global_offset,
            "tool_current": self.tool_current,
            "saved_fan_speed": self.saved_fan_speed,
            "purge_on_toolchange": self.purge_on_toolchange,
            "restore_axis_on_toolchange": self.restore_axis_on_toolchange,
            "saved_position": self.saved_position,
            "last_endstop_query": self.last_endstop_query
        }
        return status

    cmd_KTCC_SET_GCODE_OFFSET_FOR_CURRENT_TOOL_help = "Set G-Code offset to the one of current tool."
    def cmd_KTCC_SET_GCODE_OFFSET_FOR_CURRENT_TOOL(self, gcmd):
        current_tool_id = int(self.get_status()['tool_current'])

        if current_tool_id <= self.TOOL_UNLOCKED:
            msg = "KTCC_SET_GCODE_OFFSET_FOR_CURRENT_TOOL: Unknown tool mounted. Can't set offsets."
            self.log.always(msg)
        else:
            param_Move = gcmd.get_int('MOVE', 0, minval=0, maxval=1)
            current_tool = self.printer.lookup_object('tool ' + str(current_tool_id))
            self.gcode.run_script_from_command(
                f"SET_GCODE_OFFSET X={current_tool.offset[0]} Y={current_tool.offset[1]} Z={current_tool.offset[2]} MOVE={param_Move}"
            )

    ###########################################
    # TOOL REMAPING                           #
    ###########################################

    def _set_tool_to_tool(self, from_tool, to_tool):
        tools = self.printer.lookup_objects('tool')
        if not [item for item in tools if item[0] == ("tool " + str(to_tool))]:
            self.log.always(f"Tool {to_tool} not a valid tool")
            return False
        self.tool_map[from_tool] = to_tool
        self.gcode.run_script_from_command(f"SAVE_VARIABLE VARIABLE={self.VARS_KTCC_TOOL_MAP} VALUE='{self.tool_map}'")

    def _tool_map_to_human_string(self):
        msg = f"Number of tools remapped: {len(self.tool_map)}"
        for from_tool, to_tool in self.tool_map.items():
            msg += f"\nTool {from_tool} -> Tool {to_tool}"
        return msg

    def tool_is_remaped(self, tool_to_check):
        return self.tool_map.get(tool_to_check, -1)

    def _remap_tool(self, tool, gate, available):
        self._set_tool_to_tool(tool, gate)

    def _reset_tool_mapping(self):
        self.log.debug("Resetting Tool map")
        self.tool_map = {}
        self.gcode.run_script_from_command(f"SAVE_VARIABLE VARIABLE={self.VARS_KTCC_TOOL_MAP} VALUE='{self.tool_map}'")

    ### GCODE COMMANDS FOR TOOL REMAP LOGIC ##################################

    cmd_KTCC_DISPLAY_TOOL_MAP_help = "Display the current mapping of tools to other KTCC tools."
    def cmd_KTCC_DISPLAY_TOOL_MAP(self, gcmd):
        summary = gcmd.get_int('SUMMARY', 0, minval=0, maxval=1)
        self.log.always(self._tool_map_to_human_string())

    cmd_KTCC_REMAP_TOOL_help = "Remap a tool to another one."
    def cmd_KTCC_REMAP_TOOL(self, gcmd):
        reset = gcmd.get_int('RESET', 0, minval=0, maxval=1)
        if reset == 1:
            self._reset_tool_mapping()
        else:
            from_tool = gcmd.get_int('TOOL', -1, minval=0)
            to_tool = gcmd.get_int('SET', minval=0)
            available = 1
            if from_tool != -1:
                self._remap_tool(from_tool, to_tool, available)
        self.log.info(self._tool_map_to_human_string())

    ### GCODE COMMANDS FOR waiting on endstop (Jubilee style toollock) ##################################

    cmd_KTCC_ENDSTOP_QUERY_help = "Wait for a specified ENDSTOP to reach TRIGGERED state."
    def cmd_KTCC_ENDSTOP_QUERY(self, gcmd):
        endstop_name = gcmd.get('ENDSTOP')
        should_be_triggered = bool(gcmd.get_int('TRIGGERED', 1, minval=0, maxval=1))
        attempts = gcmd.get_int('ATTEMPTS', -1, minval=1)
        self.query_endstop(endstop_name, should_be_triggered, attempts)

    def query_endstop(self, endstop_name, should_be_triggered=True, attempts=-1):
        endstop = None
        query_endstops = self.printer.lookup_object('query_endstops')
        for es, name in query_endstops.endstops:
            if name == endstop_name:
                endstop = es
                break
        if endstop is None:
            raise Exception(f"Unknown endstop '{endstop_name}'")

        toolhead = self.printer.lookup_object("toolhead")
        eventtime = self.reactor.monotonic()
        dwell = 1.0 if attempts == -1 else 0.1
        i = 0

        while not self.printer.is_shutdown():
            i += 1
            last_move_time = toolhead.get_last_move_time()
            is_triggered = bool(endstop.query_endstop(last_move_time))
            self.log.trace(f"Check #{i} of {endstop_name} endstop: {'Triggered' if is_triggered else 'Not Triggered'}")
            if is_triggered == should_be_triggered:
                break
            if attempts > 0 and attempts <= i:
                break
            eventtime = self.reactor.pause(eventtime + dwell)
        self.last_endstop_query[endstop_name] = is_triggered

# Parses legacy type into string of axis names.
def parse_restore_type(gcmd, arg_name, default=None):
    type = gcmd.get(arg_name, None)
    if type is None:
        return default
    elif type == '0':
        return ''
    elif type == '1':
        return 'XY'
    elif type == '2':
        return 'XYZ'
    for c in type:
        if c not in XYZ_TO_INDEX:
            raise gcmd.error("Invalid RESTORE_POSITION_TYPE")
    return type

XYZ_TO_INDEX = {'x': 0, 'X': 0, 'y': 1, 'Y': 1, 'z': 2, 'Z': 2}
INDEX_TO_XYZ = ['X', 'Y', 'Z']

def load_config(config):
    return ToolLock(config)
