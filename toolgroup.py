# KTCC - Klipper Tool Changer Code
# ToolGroup module, used to group Tools and derived from Tool.
#
# Copyright (C) 2023 Andrei Ignat <andrei@ignat.se>
# This file may be distributed under the terms of the GNU GPLv3 license.

class ToolGroup:
    def __init__(self, config):
        self.printer = config.get_printer()
        
        # Ensure ToolGroup name uses an integer suffix
        try:
            _, name = config.get_name().split(' ', 1)
            self.name = int(name)
        except ValueError:
            raise config.error(
                f"Name of section '{config.get_name()}' contains illegal characters. Use only an integer ToolGroup number."
            )

        # Configuration parameters with defaults and type checks
        self.is_virtual = config.getboolean('is_virtual', False)
        self.physical_parent_id = config.getint('physical_parent', None)
        
        if self.is_virtual and self.physical_parent_id is None:
            raise config.error("A virtual ToolGroup must have a physical_parent defined.")
        
        self.lazy_home_when_parking = config.getint('lazy_home_when_parking', 0)
        self.pickup_gcode = config.get('pickup_gcode', default='')
        self.dropoff_gcode = config.get('dropoff_gcode', default='')
        self.virtual_toolload_gcode = config.get('virtual_toolload_gcode', default='')
        self.virtual_toolunload_gcode = config.get('virtual_toolunload_gcode', default='')
        self.meltzonelength = config.getint('meltzonelength', 0)
        
        # Validate idle timings with min values
        self.idle_to_standby_time = config.getfloat('idle_to_standby_time', 30)
        if self.idle_to_standby_time < 0.1:
            raise config.error("idle_to_standby_time must be at least 0.1 seconds.")
        
        self.idle_to_powerdown_time = config.getfloat('idle_to_powerdown_time', 600)
        if self.idle_to_powerdown_time < 0.1:
            raise config.error("idle_to_powerdown_time must be at least 0.1 seconds.")

        # Additional tool group behavior settings
        self.requires_pickup_for_virtual_load = config.getboolean("requires_pickup_for_virtual_load", True)
        self.requires_pickup_for_virtual_unload = config.getboolean("requires_pickup_for_virtual_unload", True)
        self.unload_virtual_at_dropoff = config.getboolean("unload_virtual_at_dropoff", True)

        # Optional: Add logging to verify initialization
        logger = self.printer.lookup_object('logger')
        logger.info(f"ToolGroup {self.name} initialized with is_virtual={self.is_virtual}, physical_parent_id={self.physical_parent_id}, and meltzonelength={self.meltzonelength}.")

    def get_config(self, config_param, default=None):
        return self.config.get(config_param, default)
        
    def get_status(self, eventtime=None):
        return {
            "is_virtual": self.is_virtual,
            "physical_parent_id": self.physical_parent_id,
            "lazy_home_when_parking": self.lazy_home_when_parking,
            "meltzonelength": self.meltzonelength,
            "idle_to_standby_time": self.idle_to_standby_time,
            "idle_to_powerdown_time": self.idle_to_powerdown_time,
            "requires_pickup_for_virtual_load": self.requires_pickup_for_virtual_load,
            "requires_pickup_for_virtual_unload": self.requires_pickup_for_virtual_unload,
            "unload_virtual_at_dropoff": self.unload_virtual_at_dropoff
        }

def load_config_prefix(config):
    return ToolGroup(config)
