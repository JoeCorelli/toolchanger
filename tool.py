# KTCC - Klipper Tool Changer Code
# Tool module, for each tool.
#
# Copyright (C) 2023  Andrei Ignat <andrei@ignat.se>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# To try to keep terms apart:
# Mount: Tool is selected and loaded for use, be it a physical or a virtual on physical.
# Unmopunt: Tool is unselected and unloaded, be it a physical or a virtual on physical.
# Pickup: Tool is physically picked up and attached to the toolchanger head.
# Droppoff: Tool is physically parked and dropped of the toolchanger head.
# ToolLock: Toollock is engaged.
# ToolUnLock: Toollock is disengaged.

# KTCC exception error class
# class KTCCError(Exception):
#     pass

# Each tool is getting an instance of this.
import logging
from .toollock import parse_restore_type

class Tool:
    TOOL_UNKNOWN = -2
    TOOL_UNLOCKED = -1
    HEATER_STATE_ACTIVE = 2
    HEATER_STATE_STANDBY = 1
    HEATER_STATE_OFF = 0

        def __init__(self, config=None):
        self.name = None
        self.toolgroup = None               # defaults to 0. Check if tooltype is defined.
        self.is_virtual = None
        self.physical_parent_id = None      # Parent tool is used as a Physical parent for all tools of this group.
        self.extruder = None                # Name of extruder connected to this tool. Defaults to None.
        self.fan = None                     # Name of fan configuration connected to this tool as a part fan.
        self.meltzonelength = None          # Length of the meltzone for retracting and inserting filament on toolchange.
        self.lazy_home_when_parking = None  # (default: 0 - disabled). Controls homing on parking.
        self.zone = None                    # Position of the parking zone in the format X, Y  
        self.park = None                    # Position to move to when fully parking the tool in the dock
        self.offset = None                  # Offset of the nozzle in the format X, Y, Z
        self.pickup_gcode = None            # The plain gcode string for pickup of the tool.
        self.dropoff_gcode = None           # The plain gcode string for droppoff of the tool.
        self.virtual_toolload_gcode = None  # The plain gcode string to load a virtual tool having this tool as parent.
        self.virtual_toolunload_gcode = None# The plain gcode string to unload for virtual tool having this tool as parent.
        self.requires_pickup_for_virtual_load = None   # Needed for filament swap to prevent ooze but not for a pen.
        self.requires_pickup_for_virtual_unload = None # Needed for filament swap to prevent ooze but not for a pen.
        self.unload_virtual_at_dropoff = None          # Leave virtual tool loaded, unload at end of print.
        self.virtual_loaded = -1            # The abstract tool loaded in the physical tool.
        self.heater_state = 0               # 0 = off, 1 = standby temperature, 2 = active temperature.
        self.heater_active_temp = 0         # Temperature to set when in active mode.
        self.heater_standby_temp = 0        # Temperature to set when in standby mode.
        self.idle_to_standby_time = None    # Time from parking to setting temperature to standby.
        self.idle_to_powerdown_time = None  # Time from parking to setting temperature to 0.
        self.shaper_freq_x = 0
        self.shaper_freq_y = 0
        self.shaper_type_x = "mzv"
        self.shaper_type_y = "mzv"
        self.shaper_damping_ratio_x = 0.1
        self.shaper_damping_ratio_y = 0.1
        self.config = config

        # Load used objects.
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.toollock = self.printer.lookup_object('toollock')
        self.log = self.printer.lookup_object('ktcclog')

        ##### Name #####
        try:
            _, name = config.get_name().split(" ", 1)
            self.name = int(name)
        except ValueError:
            raise config.error(
                    "Name of section '%s' contains illegal characters. Use only integer tool number."
                    % (config.get_name()))

        # Caching toolgroup and physical parent ID for efficiency
        ##### ToolGroup #####
        self.toolgroup = 'toolgroup ' + str(config.getint('tool_group'))
        if config.has_section(self.toolgroup):
            self.cached_toolgroup = self.printer.lookup_object(self.toolgroup)
        else:
            raise config.error(
                f"ToolGroup of T'{config.get_name()}' is not defined. It must be configured before the tool."
            )

        ##### Physical Parent #####
self.physical_parent_id = config.getint('physical_parent', self.cached_toolgroup.get_status()["physical_parent_id"])
self.cached_physical_parent_id = self.physical_parent_id if self.physical_parent_id is not None else self.TOOL_UNLOCKED

# Initialize the physical parent object if applicable
if self.cached_physical_parent_id >= 0 and self.cached_physical_parent_id != self.name:
    self.pp = self.printer.lookup_object("tool " + str(self.cached_physical_parent_id))
else:
    self.pp = Tool()  # Initialize physical parent as a dummy object.

pp_status = self.pp.get_status()

# Sanity check for tools that are virtual but lack a valid physical parent
if self.is_virtual and self.cached_physical_parent_id == self.TOOL_UNLOCKED:
    raise config.error(
        "Section Tool '%s' cannot be virtual without a valid physical_parent. If Virtual and Physical, use itself as parent."
        % (config.get_name())
    )
        
        ##### Is Virtual #####
        self.is_virtual = config.getboolean('is_virtual', 
                                            tg_status["is_virtual"])
        ##### Extruder #####
        self.extruder = config.get('extruder', pp_status['extruder'])      

        ##### Fan #####
        self.fan = config.get('fan', pp_status['fan'])                     

        ##### Meltzone Length #####
        self.meltzonelength = self._get_config_parameter_with_inheritence('meltzonelength', 0)

        ##### Lazy Home when parking #####
        self.lazy_home_when_parking = self._get_bool_config_parameter_with_inheritence('lazy_home_when_parking', False)

        ##### Coordinates #####
        try:
            self.zone = config.get('zone', pp_status['zone'])
            if not isinstance(self.zone, list):
                self.zone = str(self.zone).split(',')
            self.park = config.get('park', pp_status['park'])                  
            if not isinstance(self.park, list):
                self.park = str(self.park).split(',')
            self.offset = config.get('offset', pp_status['offset'])
            if not isinstance(self.offset, list):
                self.offset = str(self.offset).split(',')

            # Remove any accidental blank spaces.
            self.zone = [s.strip() for s in self.zone]
            self.park = [s.strip() for s in self.park]
            self.offset = [s.strip() for s in self.offset]

            if len(self.zone) < 3:
                raise config.error("zone Offset is malformed, must be a list of x,y,z If you want it blank, use 0,0,0")
            if len(self.park) < 3:
                raise config.error("park Offset is malformed, must be a list of x,y,z If you want it blank, use 0,0,0")
            if len(self.offset) < 3:
                raise config.error("offset Offset is malformed, must be a list of x,y,z. If you want it blank, use 0,0,0")

        except Exception as e:
            raise config.error(
                    "Coordinates of section '%s' is not well formated: %s"
                    % (config.get_name(), str(e)))

        # Tool specific input shaper parameters. Initiated with Klipper standard values where not specified.
        self.shaper_freq_x = config.get('shaper_freq_x', pp_status['shaper_freq_x'])                     
        self.shaper_freq_y = config.get('shaper_freq_y', pp_status['shaper_freq_y'])                     
        self.shaper_type_x = config.get('shaper_type_x', pp_status['shaper_type_x'])                     
        self.shaper_type_y = config.get('shaper_type_y', pp_status['shaper_type_y'])                     
        self.shaper_damping_ratio_x = config.get('shaper_damping_ratio_x', pp_status['shaper_damping_ratio_x'])                     
        self.shaper_damping_ratio_y = config.get('shaper_damping_ratio_y', pp_status['shaper_damping_ratio_y'])                     

        ##### Standby settings (if the tool has an extruder) #####
        if self.extruder is not None:
            self.idle_to_standby_time = self.config.getfloat(
                "idle_to_standby_time", self.pp.idle_to_standby_time)
            if self.idle_to_standby_time is None:
                self.idle_to_standby_time = self.toolgroup.idle_to_standby_time

            self.idle_to_powerdown_time = self.config.getfloat(
                "idle_to_powerdown_time", self.pp.idle_to_powerdown_time)
            if self.idle_to_powerdown_time is None:
                self.idle_to_powerdown_time = self.toolgroup.idle_to_powerdown_time

            # For all virtual tools that are not also a physical parent, use physical parent's timer.
            if self.physical_parent_id > self.TOOL_UNLOCKED and self.physical_parent_id != self.name:
                self.timer_idle_to_standby = self.pp.get_timer_to_standby()
                self.timer_idle_to_powerdown = self.pp.get_timer_to_powerdown()
            else:
                # Set up new timers if physical tool.
                self.timer_idle_to_standby = ToolStandbyTempTimer(self.printer, self.name, ToolStandbyTempTimer.TIMER_TO_STANDBY)
                self.timer_idle_to_powerdown = ToolStandbyTempTimer(self.printer, self.name, ToolStandbyTempTimer.TIMER_TO_SHUTDOWN)

        ##### G-Code ToolChange #####
        self.pickup_gcode_template = self._get_gcode_template_with_inheritence('pickup_gcode')
        self.dropoff_gcode_template = self._get_gcode_template_with_inheritence('dropoff_gcode')

        ##### G-Code VirtualToolChange #####
        if self.is_virtual:
            self.virtual_toolload_gcode_template = self._get_gcode_template_with_inheritence('virtual_toolload_gcode')
            self.virtual_toolunload_gcode_template = self._get_gcode_template_with_inheritence('virtual_toolunload_gcode')

        ##### Parameters for VirtualToolChange #####
            self.requires_pickup_for_virtual_load = self.config.getboolean(
                "requires_pickup_for_virtual_load", self.pp.requires_pickup_for_virtual_load)
            if self.requires_pickup_for_virtual_load is None:
                self.requires_pickup_for_virtual_load = self.toolgroup.requires_pickup_for_virtual_load

            self.requires_pickup_for_virtual_unload = self.config.getboolean(
                "requires_pickup_for_virtual_unload", self.pp.requires_pickup_for_virtual_unload)
            if self.requires_pickup_for_virtual_unload is None:
                self.requires_pickup_for_virtual_unload = self.toolgroup.requires_pickup_for_virtual_unload

            self.unload_virtual_at_dropoff = self.config.getboolean(
                "unload_virtual_at_dropoff", self.pp.unload_virtual_at_dropoff)
            if self.unload_virtual_at_dropoff is None:
                self.unload_virtual_at_dropoff = self.toolgroup.unload_virtual_at_dropoff

        logging.warn("T%s unload_virtual_at_dropoff: %s" % (str(self.name), str(self.requires_pickup_for_virtual_load)))
            
        ##### Register Tool select command #####
        self.gcode.register_command("KTCC_T" + str(self.name), self.cmd_SelectTool, desc=self.cmd_SelectTool_help)

    def _get_bool_config_parameter_with_inheritence(self, config_param, default = None):
        tmp = self.config.getboolean(config_param, self.pp.get_config(config_param))   
        if tmp is None:
            tmp = self.toolgroup.get_config(config_param, default)
        return tmp

    def _get_config_parameter_with_inheritence(self, config_param, default = None):
        tmp = self.config.get(config_param, self.pp.get_config(config_param))   
        if tmp is None:
            tmp = self.toolgroup.get_config(config_param, default)
        return tmp

    def _get_gcode_template_with_inheritence(self, config_param, optional = False):
        temp_gcode = self.pp.get_config(config_param)                   # First try to get gcode parameter from eventual physical Parent.
        if temp_gcode is None:                                          # If didn't get any from physical parent,
            temp_gcode =  self.toolgroup.get_config(config_param)       # try getting from toolgroup.

        if optional and temp_gcode is None:
            temp_gcode = ""

        # gcode = self.get_config(config_param, temp_gcode)               # Get from this config and fallback on previous.
        template = self.gcode_macro.load_template(self.config, config_param, temp_gcode)
        return template

    def get_config(self, config_param, default = None):
        if self.config is None: return None
        return self.config.get(config_param, default)
        
    cmd_SelectTool_help = "Select Tool"
    def cmd_SelectTool(self, gcmd):
        self.log.trace("KTCC T" + str(self.name) + " Selected.")
        # Allow either one.
        restore_mode = parse_restore_type(gcmd, 'R', None)
        restore_mode = parse_restore_type(gcmd, 'RESTORE_POSITION_TYPE', restore_mode)

        # Check if the requested tool has been remaped to another one.
        tool_is_remaped = self.toollock.tool_is_remaped(int(self.name))

        if tool_is_remaped > -1:
            self.log.always("Tool %d is remaped to Tool %d" % (self.name, tool_is_remaped))
            remaped_tool = self.printer.lookup_object('tool ' + str(tool_is_remaped))
            remaped_tool.select_tool_actual(restore_mode)
            return
        else:
            self.select_tool_actual(restore_mode)
            

    # To avoid recursive remaping.
    def select_tool_actual(self, restore_mode = None):
        current_tool_id = int(self.toollock.get_status()['tool_current']) # int(self.toollock.get_tool_current())

        self.log.trace("Current Tool is T" + str(current_tool_id) + ".")
        self.log.trace("This tool is_virtual is " + str(self.is_virtual) + ".")

        if current_tool_id == self.name:              # If trying to select the already selected tool:
            return                                      # Exit

        if current_tool_id < self.TOOL_UNLOCKED:
            msg = "TOOL_PICKUP: Unknown tool already mounted Can't park it before selecting new tool."
            self.log.always(msg)
            raise self.printer.command_error(msg)
        
        self.log.increase_tool_statistics(self.name, 'toolmounts_started')


        if self.extruder is not None:               # If the new tool to be selected has an extruder prepare warmup before actual tool change so all unload commands will be done while heating up.
            self.set_heater(heater_state = self.HEATER_STATE_ACTIVE)

        # If optional RESTORE_POSITION_TYPE parameter is passed then save current position.
        # Otherwise do not change either the restore_axis_on_toolchange or saved_position.
        # This makes it possible to call SAVE_POSITION or SAVE_CURRENT_POSITION before the actual T command.
        if restore_mode is not None:
            self.toollock.SaveCurrentPosition(restore_mode) # Sets restore_axis_on_toolchange and saves current position

        # Drop any tools already mounted if not virtual on same.
        if current_tool_id > self.TOOL_UNLOCKED:              # If there is a current tool already selected and it's a known tool.
            self.log.track_selected_tool_end(current_tool_id) # Log that the current tool is to be unmounted.

            current_tool = self.printer.lookup_object('tool ' + str(current_tool_id))
           
            # If the next tool is not another virtual tool on the same physical tool.
            if int(self.physical_parent_id ==  self.TOOL_UNLOCKED or 
                        self.physical_parent_id) !=  int( 
                        current_tool.get_status()["physical_parent_id"]
                        ):
                self.log.info("Will Dropoff():%s" % str(current_tool_id))
                current_tool.Dropoff()
                current_tool_id = self.TOOL_UNLOCKED
            else: # If it's another virtual tool on the same parent physical tool.
                self.log.info("Dropoff: T" + str(current_tool_id) + "- Virtual - Running UnloadVirtual")
                current_tool.UnloadVirtual()



        # Now we asume tool has been dropped if needed be.

        # Check if this is a virtual tool.
        if not self.is_virtual:
            self.log.trace("cmd_SelectTool: T%s - Not Virtual - Pickup" % str(self.name))
            self.Pickup()
        else:
            if current_tool_id > self.TOOL_UNLOCKED:                 # If still has a selected tool: (This tool is a virtual tool with same physical tool as the last)
                current_tool = self.printer.lookup_object('tool ' + str(current_tool_id))
                self.log.trace("cmd_SelectTool: T" + str(self.name) + "- Virtual - Physical Tool is not Dropped - ")
                if self.physical_parent_id > self.TOOL_UNLOCKED and self.physical_parent_id == current_tool.get_status()["physical_parent_id"]:
                    self.log.trace("cmd_SelectTool: T" + str(self.name) + "- Virtual - Same physical tool - Pickup")
                    self.LoadVirtual()
                else:
                    msg = "cmd_SelectTool: T" + str(self.name) + "- Virtual - Not Same physical tool"
                    msg += "Shouldn't reach this because it is dropped in previous."
                    self.log.debug(msg)
                    raise Exception(msg)
            else: # New Physical tool with a virtual tool.
                pp = self.printer.lookup_object('tool ' + str(self.physical_parent_id))
                pp_virtual_loaded = pp.get_status()["virtual_loaded"]
                self.log.trace("cmd_SelectTool: T" + str(self.name) + "- Virtual - Picking upp physical tool")
                self.Pickup()

                # If the new physical tool already has another virtual tool loaded:
                if pp_virtual_loaded > self.TOOL_UNLOCKED:
                    if pp_virtual_loaded != self.name:
                        self.log.info("cmd_SelectTool: T" + str(pp_virtual_loaded) + "- Virtual - Running UnloadVirtual")

                        uv= self.printer.lookup_object('tool ' + str(pp_virtual_loaded))
                        if uv.extruder is not None:               # If the new tool to be selected has an extruder prepare warmup before actual tool change so all unload commands will be done while heating up.
                            curtime = self.printer.get_reactor().monotonic()
                            # heater = self.printer.lookup_object(self.extruder).get_heater()

                            uv.set_heater(heater_state = self.HEATER_STATE_ACTIVE)
                            # if int(self.heater_state) == self.HEATER_STATE_ACTIVE and int(self.heater_standby_temp) < int(heater.get_status(curtime)["temperature"]):
                            self.toollock._Temperature_wait_with_tolerance(curtime, self.extruder, 2)
                        uv.UnloadVirtual()
                        self.set_heater(heater_state = self.HEATER_STATE_ACTIVE)


                self.log.trace("cmd_SelectTool: T" + str(self.name) + "- Virtual - Picked up physical tool and now Loading virtual tool.")
                self.LoadVirtual()

        self.toollock.SaveCurrentTool(self.name)
        self.log.track_selected_tool_start(self.name)


    def Pickup(self):
    self.log.track_mount_start(self.name)  # Log time for tool mount

    # Check if homed
    if not self.toollock.PrinterIsHomedForToolchange():
        raise self.printer.command_error(
            f"Tool.Pickup: Printer not homed and Lazy homing option for tool {self.name} is: {self.lazy_home_when_parking}"
        )

    # Activate extruder if available
    if self.extruder is not None:
        self.gcode.run_script_from_command(f"ACTIVATE_EXTRUDER extruder={self.extruder}")

    # Insert a short dwell before running pickup G-code to avoid processing congestion
    self.gcode.run_script_from_command("G4 P0.2")
    
    # Run the G-code for pickup
    try:
        context = self.pickup_gcode_template.create_template_context()
        context['myself'] = self.get_status()
        context['toollock'] = self.toollock.get_status()
        self.pickup_gcode_template.run_gcode_from_command(context)
    except Exception as e:
        raise Exception(f"Pickup gcode: Script running error: {e}")

    # Restore fan speed if available
    if self.fan is not None:
        self.gcode.run_script_from_command(
            f"SET_FAN_SPEED FAN={self.fan} SPEED={self.toollock.get_status()['saved_fan_speed']}"
        )

    # Set Tool specific input shaper (deprecated)
    if self.shaper_freq_x != 0 or self.shaper_freq_y != 0:
        self.log.always("shaper_freq will be deprecated. Use SET_INPUT_SHAPER inside the pickup gcode instead.")
        cmd = ("SET_INPUT_SHAPER" +
               " SHAPER_FREQ_X=" + str(self.shaper_freq_x) +
               " SHAPER_FREQ_Y=" + str(self.shaper_freq_y) +
               " DAMPING_RATIO_X=" + str(self.shaper_damping_ratio_x) +
               " DAMPING_RATIO_Y=" + str(self.shaper_damping_ratio_y) +
               " SHAPER_TYPE_X=" + str(self.shaper_type_x) +
               " SHAPER_TYPE_Y=" + str(self.shaper_type_y))
        self.log.trace("Pickup_inpshaper: " + cmd)
        self.gcode.run_script_from_command(cmd)

    # Save the current picked-up tool
    self.toollock.SaveCurrentTool(self.name)
    if self.is_virtual:
        self.log.always("Physical Tool for T%d picked up." % (self.name))
    else:
        self.log.always("T%d picked up." % (self.name))

    self.log.track_mount_end(self.name)  # Log tool change completion

    # Conditional logging
    if self.log.is_debug_enabled():
        self.log.debug("Pickup complete.")


    def Dropoff(self, force_virtual_unload=False):
    self.log.always(f"Dropoff: T{self.name} - Running.")

    # Check if homed
    if not self.toollock.PrinterIsHomedForToolchange():
        self.log.always(f"Tool.Dropoff: Printer not homed and Lazy homing option is: {self.lazy_home_when_parking}")
        return None

    # Turn off fan if available
    if self.fan is not None:
        self.gcode.run_script_from_command(f"SET_FAN_SPEED FAN={self.fan} SPEED=0")

    # Short dwell before dropoff G-code to prevent timing issues
    self.gcode.run_script_from_command("G4 P0.2")

    # Run the G-code for dropoff
    try:
        context = self.dropoff_gcode_template.create_template_context()
        context['myself'] = self.get_status()
        context['toollock'] = self.toollock.get_status()
        self.dropoff_gcode_template.run_gcode_from_command(context)
    except Exception as e:
        raise Exception(f"Dropoff gcode: Script running error: {e}")

    # Save current tool as unmounted
    self.toollock.SaveCurrentTool(self.TOOL_UNLOCKED)

    # Log the unmount end time for tracking
    self.log.track_unmount_end(self.name)

    # Conditional logging to reduce verbosity unless debugging
    if self.log.is_debug_enabled():
        self.log.debug("Dropoff complete.")



    def LoadVirtual(self):
        self.log.info("Loading virtual tool: T%d." % self.name)
        self.log.track_mount_start(self.name)                 # Log the time it takes for tool mount.

        # Run the gcode for Virtual Load.
        try:
            context = self.virtual_toolload_gcode_template.create_template_context()
            context['myself'] = self.get_status()
            context['toollock'] = self.toollock.get_status()
            self.virtual_toolload_gcode_template.run_gcode_from_command(context)
        except Exception as e:
            raise Exception("virtual_toolload_gcode: Script running error: %s" % (str(e)))

        pp = self.printer.lookup_object('tool ' + str(self.physical_parent_id))
        pp.set_virtual_loaded(int(self.name))

        # Save current picked up tool and print on screen.
        self.toollock.SaveCurrentTool(self.name)
        self.log.trace("Virtual T%d Loaded" % (int(self.name)))
        self.log.track_mount_end(self.name)             # Log number of toolchanges and the time it takes for tool mounting.

    def set_virtual_loaded(self, value = -1):
        self.virtual_loaded = value
        self.log.trace("Saved VirtualToolLoaded for T%s as: %s" % (str(self.name), str(value)))


    def UnloadVirtual(self):
        self.log.info("Unloading virtual tool: T%d." % self.name)
        self.log.track_unmount_start(self.name)                 # Log the time it takes for tool unload.

        # Run the gcode for Virtual Unload.
        try:
            context = self.virtual_toolunload_gcode_template.create_template_context()
            context['myself'] = self.get_status()
            context['toollock'] = self.toollock.get_status()
            self.virtual_toolunload_gcode_template.run_gcode_from_command(context)
        except Exception as e:
            raise Exception("virtual_toolunload_gcode: Script running error:\n%s" % str(e))

        pp = self.printer.lookup_object('tool ' + str(self.physical_parent_id))
        pp.set_virtual_loaded(-1)

        # Save current picked up tool and print on screen.
        self.toollock.SaveCurrentTool(self.name)
        self.log.trace("Virtual T%d Unloaded" % (int(self.name)))

        self.log.track_unmount_end(self.name)                 # Log the time it takes for tool unload. 

    def set_offset(self, **kwargs):
        for i in kwargs:
            if i == "x_pos":
                self.offset[0] = float(kwargs[i])
            elif i == "x_adjust":
                self.offset[0] = float(self.offset[0]) + float(kwargs[i])
            elif i == "y_pos":
                self.offset[1] = float(kwargs[i])
            elif i == "y_adjust":
                self.offset[1] = float(self.offset[1]) + float(kwargs[i])
            elif i == "z_pos":
                self.offset[2] = float(kwargs[i])
            elif i == "z_adjust":
                self.offset[2] = float(self.offset[2]) + float(kwargs[i])

        self.log.always("T%d offset now set to: %f, %f, %f." % (int(self.name), float(self.offset[0]), float(self.offset[1]), float(self.offset[2])))

    def _set_state(self, heater_state):
        self.heater_state = heater_state


    def set_heater(self, **kwargs):
        if self.extruder is None:
            self.log.debug("set_heater: T%d has no extruder! Nothing to do." % self.name )
            return None

        # self.log.info("T%d heater is at begingin %s." % (self.name, self.heater_state ))

        heater = self.printer.lookup_object(self.extruder).get_heater()
        curtime = self.printer.get_reactor().monotonic()
        changing_timer = False
        
        # self is always pointing to virtual tool but its timers and extruder are always pointing to the physical tool. When changing multiple virtual tools heaters the statistics can remain open when changing by timers of the parent if another one got in between.
        # Therefore it's important for all heater statistics to only point to physical parent.

        if self.is_virtual == True:
            tool_for_tracking_heater = self.physical_parent_id
        else:
            tool_for_tracking_heater = self.name

        # First set state if changed, so we set correct temps.
        if "heater_state" in kwargs:
            chng_state = kwargs["heater_state"]
        for i in kwargs:
            if i == "heater_active_temp":
                self.heater_active_temp = kwargs[i]
                if int(self.heater_state) == self.HEATER_STATE_ACTIVE:
                    heater.set_temp(self.heater_active_temp)
            elif i == "heater_standby_temp":
                self.heater_standby_temp = kwargs[i]
                if int(self.heater_state) == self.HEATER_STATE_STANDBY:
                    heater.set_temp(self.heater_standby_temp)
            elif i == "idle_to_standby_time":
                self.idle_to_standby_time = kwargs[i]
                changing_timer = True
            elif i == "idle_to_powerdown_time":
                self.idle_to_powerdown_time = kwargs[i]
                changing_timer = True

        # If already in standby and timers are counting down, i.e. have not triggered since set in standby, then reset the ones counting down.
        if int(self.heater_state) == self.HEATER_STATE_STANDBY and changing_timer:
            if self.timer_idle_to_powerdown.get_status()["counting_down"] == True:
                self.timer_idle_to_powerdown.set_timer(self.idle_to_powerdown_time, self.name)
                if self.idle_to_powerdown_time > 2:
                    self.log.info("T%d heater will shut down in %s seconds." % (self.name, self.log._seconds_to_human_string(self.idle_to_powerdown_time) ))
            if self.timer_idle_to_standby.get_status()["counting_down"] == True:
                self.timer_idle_to_standby.set_timer(self.idle_to_standby_time, self.name)
                if self.idle_to_standby_time > 2:
                    self.log.info("T%d heater will go in standby in %s seconds." % (self.name, self.log._seconds_to_human_string(self.idle_to_standby_time) ))


        # Change Active mode, Continuing with part two of temp changing.:
        if "heater_state" in kwargs:
            if self.heater_state == chng_state:                                                         # If we don't actually change the state don't do anything.
                if chng_state == self.HEATER_STATE_ACTIVE:
                    self.log.trace("set_heater: T%d heater state not changed. Setting active temp." % self.name )
                    heater.set_temp(self.heater_active_temp)
                elif chng_state == self.HEATER_STATE_STANDBY:
                    self.log.trace("set_heater: T%d heater state not changed. Setting standby temp." % self.name )
                    heater.set_temp(self.heater_standby_temp)
                else:
                    self.log.trace("set_heater: T%d heater state not changed." % self.name )
                return None
            if chng_state == self.HEATER_STATE_OFF:                                                                         # If Change to Shutdown
                self.log.trace("set_heater: T%d heater state now OFF." % self.name )
                self.timer_idle_to_standby.set_timer(0, self.name)
                self.timer_idle_to_powerdown.set_timer(0.1, self.name)
                # self.log.track_standby_heater_end(self.name)                                                # Set the standby as finishes in statistics.
                # self.log.track_active_heater_end(self.name)                                                # Set the active as finishes in statistics.
            elif chng_state == self.HEATER_STATE_ACTIVE:                                                                       # Else If Active
                self.log.trace("set_heater: T%d heater state now ACTIVE." % self.name )
                self.timer_idle_to_standby.set_timer(0, self.name)
                self.timer_idle_to_powerdown.set_timer(0, self.name)
                heater.set_temp(self.heater_active_temp)
                self.log.track_standby_heater_end(tool_for_tracking_heater)                                                # Set the standby as finishes in statistics.
                self.log.track_active_heater_start(tool_for_tracking_heater)                                               # Set the active as started in statistics.
            elif chng_state == self.HEATER_STATE_STANDBY:                                                                       # Else If Standby
                self.log.trace("set_heater: T%d heater state now STANDBY." % self.name )
                if int(self.heater_state) == self.HEATER_STATE_ACTIVE and int(self.heater_standby_temp) < int(heater.get_status(curtime)["temperature"]):
                    self.timer_idle_to_standby.set_timer(self.idle_to_standby_time, self.name)
                    self.timer_idle_to_powerdown.set_timer(self.idle_to_powerdown_time, self.name)
                    if self.idle_to_standby_time > 2:
                        self.log.always("T%d heater will go in standby in %s seconds." % (self.name, self.log._seconds_to_human_string(self.idle_to_standby_time) ))
                else:                                                                                   # Else (Standby temperature is lower than the current temperature)
                    self.log.trace("set_heater: T%d standbytemp:%d;heater_state:%d; current_temp:%d." % (self.name, int(self.heater_state), int(self.heater_standby_temp), int(heater.get_status(curtime)["temperature"])))
                    self.timer_idle_to_standby.set_timer(0.1, self.name)
                    self.timer_idle_to_powerdown.set_timer(self.idle_to_powerdown_time, self.name)
                if self.idle_to_powerdown_time > 2:
                    self.log.always("T%d heater will shut down in %s seconds." % (self.name, self.log._seconds_to_human_string(self.idle_to_powerdown_time)))
            self.heater_state = chng_state


    def get_timer_to_standby(self):
        return self.timer_idle_to_standby

    def get_timer_to_powerdown(self):
        return self.timer_idle_to_powerdown

    def get_status(self, eventtime= None):
        status = {
            "name": self.name,
            "is_virtual": self.is_virtual,
            "physical_parent_id": self.physical_parent_id,
            "extruder": self.extruder,
            "fan": self.fan,
            "lazy_home_when_parking": self.lazy_home_when_parking,
            "meltzonelength": self.meltzonelength,
            "zone": self.zone,
            "park": self.park,
            "offset": self.offset,
            "heater_state": self.heater_state,
            "heater_active_temp": self.heater_active_temp,
            "heater_standby_temp": self.heater_standby_temp,
            "idle_to_standby_time": self.idle_to_standby_time,
            "idle_to_powerdown_next_wake": self.idle_to_powerdown_time,
            "shaper_freq_x": self.shaper_freq_x,
            "shaper_freq_y": self.shaper_freq_y,
            "shaper_type_x": self.shaper_type_x,
            "shaper_type_y": self.shaper_type_y,
            "shaper_damping_ratio_x": self.shaper_damping_ratio_x,
            "shaper_damping_ratio_y": self.shaper_damping_ratio_y,
            "virtual_loaded": self.virtual_loaded,
            "requires_pickup_for_virtual_load": self.requires_pickup_for_virtual_load,
            "requires_pickup_for_virtual_unload": self.requires_pickup_for_virtual_unload,
            "unload_virtual_at_dropoff": self.unload_virtual_at_dropoff
        }
        return status

    # Based on DelayedGcode.
class ToolStandbyTempTimer:
    TIMER_TO_SHUTDOWN = 0
    TIMER_TO_STANDBY = 1

    def __init__(self, printer, tool_id, temp_type):
        self.printer = printer
        self.tool_id = tool_id
        self.last_virtual_tool_using_physical_timer = None
        self.duration = 0.
        self.temp_type = temp_type      # 0= Time to shutdown, 1= Time to standby.
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.timer_handler = None
        self.inside_timer = self.repeat = False
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.toollock = self.printer.lookup_object('toollock')
        self.log = self.printer.lookup_object('ktcclog')
        self.counting_down = False
        self.nextwake = self.reactor.NEVER


    def _handle_ready(self):
        self.timer_handler = self.reactor.register_timer(
            self._standby_tool_temp_timer_event, self.reactor.NEVER)

    def _standby_tool_temp_timer_event(self, eventtime):
        self.inside_timer = True
        self.counting_down = False
        try:
            if self.last_virtual_tool_using_physical_timer is None:
                raise Exception("last_virtual_tool_using_physical_timer is < None")

            tool = self.printer.lookup_object("tool " + str(self.last_virtual_tool_using_physical_timer))
            if tool.is_virtual == True:
                tool_for_tracking_heater = tool.physical_parent_id
            else:
                tool_for_tracking_heater = tool.name



            self.log.trace(
                "_standby_tool_temp_timer_event: Running for T%s. temp_type:%s. %s" % 
                (str(self.tool_id), 
                 "Time to shutdown" if self.temp_type == 0 else "Time to standby", 
                 ("For virtual tool T%s" % str(self.last_virtual_tool_using_physical_timer) ) 
                 if  self.last_virtual_tool_using_physical_timer != self.tool_id else ""))

            temperature = 0
            heater = self.printer.lookup_object(tool.extruder).get_heater()
            if self.temp_type == self.TIMER_TO_STANDBY:
                self.log.track_standby_heater_start(self.tool_id)                                                # Set the standby as started in statistics.
                temperature = tool.get_status()["heater_standby_temp"]
                heater.set_temp(temperature)
                # self.log.trace("_standby_tool_temp_timer_event: Running heater.set_temp(%s)" % str(temperature))
            else:
                self.log.track_standby_heater_end(self.tool_id)                                                # Set the standby as finishes in statistics.

                tool.get_timer_to_standby().set_timer(0, self.last_virtual_tool_using_physical_timer)        # Stop Standby timer.
                #tool.get_timer_to_powerdown().set_timer(0, self.last_virtual_tool_using_physical_timer)        # Stop Poweroff timer. (Already off)
                tool._set_state(Tool.HEATER_STATE_OFF)        # Set off state.
                heater.set_temp(0)        # Set temperature to 0.


                # tool.set_heater(Tool.HEATER_STATE_OFF)
            self.log.track_active_heater_end(self.tool_id)                                               # Set the active as finishes in statistics.

        except Exception as e:
            raise Exception("Failed to set Standby temp for tool T%s: %s. %s" % (str(self.tool_id), 
                                                                                 ("for virtual T%s" % str(self.last_virtual_tool_using_physical_timer)),
                                                                                 str(e)))  # if actual_tool_calling != self.tool_id else ""

        self.nextwake = self.reactor.NEVER
        if self.repeat:
            self.nextwake = eventtime + self.duration
            self.counting_down = True
        self.inside_timer = self.repeat = False
        return self.nextwake

    def set_timer(self, duration, actual_tool_calling):
        min_duration_threshold = 0.5  # Minimum duration to reduce "Timer too close" issues
        duration = max(duration, min_duration_threshold)  # Ensure timer has a safe interval

        actual_tool_calling = actual_tool_calling
        self.log.trace(
            f"{self.timer_handler}.set_timer: T{self.tool_id} "
            f"{'for virtual T' + str(actual_tool_calling) if actual_tool_calling != self.tool_id else ''}, "
            f"temp_type: {'Standby' if self.temp_type == 1 else 'OFF'}, duration: {duration}."
        )

        self.duration = float(duration)
        self.last_virtual_tool_using_physical_timer = actual_tool_calling
        if self.inside_timer:
            self.repeat = (self.duration != 0.)
        else:
            waketime = self.reactor.NEVER
            if self.duration:
                waketime = self.reactor.monotonic() + self.duration
                self.nextwake = waketime
            self.reactor.update_timer(self.timer_handler, waketime)
            self.counting_down = True

    def get_status(self, eventtime= None):
        status = {
            # "tool": self.tool,
            "temp_type": self.temp_type,
            "duration": self.duration,
            "counting_down": self.counting_down,
            "next_wake": self._time_left()

        }
        return status

    def _time_left(self):
        if self.nextwake == self.reactor.NEVER:
            return "never"
        else:
            return str( self.nextwake - self.reactor.monotonic() )


    # Todo: 
    # Inspired by https://github.com/jschuh/klipper-macros/blob/main/layers.cfg
class MeanLayerTime:
    def __init__(self, printer):
        # Run before toolchange to set time like in StandbyToolTimer.
        # Save time for last 5 (except for first) layers
        # Provide a mean layer time.
        # Have Tool have a min and max 2standby time.
        # If mean time for 3 layers is higher than max, then set min time.
        # Reset time if layer time is higher than max time. Pause or anything else that has happened.
        # Function to reset layer times.
        pass


def load_config_prefix(config):
    return Tool(config)
