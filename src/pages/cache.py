#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# cache.py
#
# Copyright © 2013-2018 Antergos
#
# This file is part of Cnchi.
#
# Cnchi is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Cnchi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# The following additional terms are in effect as per Section 7 of the license:
#
# The preservation of all legal notices and author attributions in
# the material or in the Appropriate Legal Notices displayed
# by works containing it is required.
#
# You should have received a copy of the GNU General Public License
# along with Cnchi; If not, see <http://www.gnu.org/licenses/>.


""" Cache selection screen """

import logging
import os
import subprocess

import parted

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

import misc.extra as misc
import show_message as show
import parted3.fs_module as fs
import update_db
from pages.gtkbasebox import GtkBaseBox

class Cache(GtkBaseBox):
    """ Cache selection screen"""

    CACHE_DIRECTORY = "/mnt/cnchi-cache"

    def __init__(self, params, prev_page="features", next_page="mirrors"):
        super().__init__(self, params, "cache", prev_page, next_page)

        # Stores device and partition tuple that will be used as cache
        # if partition is None, cnchi will try to use the whole device
        self.cache_path = (None, None)
        self.part_store = self.ui.get_object('select_part')
        self.part_label = self.ui.get_object('select_part_label')
        self.devices_and_partitions = {}

    def translate_ui(self):
        """ Translate widgets """
        txt = _("Select cache partition:")
        self.part_label.set_markup(txt)

        label = self.ui.get_object('text_info')
        txt = _("It is recommended to use an additional cache\n"
                "This installer needs to download a TON of packages from the Internet!")
        txt = "<b>{}</b>".format(txt)
        label.set_markup(txt)

        label = self.ui.get_object('info_label')
        par1 = _("You can use an aditional device or partition to use as packages' cache. "
                 "In case you need to restart this installation\nyou won't be needing to "
                 "re-download all packages again.")
        par2 = _("- It <b>cannot</b> be the same device or partition where you "
                 "are installing Antergos.")
        par3 = _("- If you select a <b>device</b>, its contents will be fully <b>DELETED!</b>")
        par4 = _("- If you select a <b>partition</b> its contents will be <b>preserved</b> "
                 "(you must be sure that it is alread formated and unmounted!)")
        par5 = _("If this is not the first time you are running this installer you "
                 "need to select a partition, and not a drive (selecting a drive will\n"
                 "delete the packages you have already downloaded).")
        par6 = _("Please, choose now the device (or partition) to use as cache.")
        txt = "{0}\n\n{1}\n{2}\n{3}\n\n{4}\n\n{5}\n".format(
            par1, par2, par3, par4, par5, par6)
        label.set_markup(txt)

        self.header.set_subtitle(_("Cache selection (optional)"))

    def populate_devices_and_partitions(self):
        """ Fill list with devices' partitions """
        with misc.raised_privileges():
            device_list = parted.getAllDevices()

        self.part_store.remove_all()
        self.devices_and_partitions = {}

        self.part_store.append_text("None")
        self.devices_and_partitions["None"] = (None, None)

        for dev in device_list:
            # avoid cdrom and any raid, lvm volumes or encryptfs
            if not dev.path.startswith("/dev/sr") and \
               not dev.path.startswith("/dev/mapper"):
                size = dev.length * dev.sectorSize
                size_gbytes = int(parted.formatBytes(size, 'GB'))
                line = '{0} [{1} GB] ({2})'.format(dev.model, size_gbytes, dev.path)
                self.part_store.append_text(line)
                self.devices_and_partitions[line] = (dev.path, None)
                # Now check device partitions
                try:
                    disk = parted.newDisk(dev)
                    for partition in disk.partitions:
                        if partition.type in [parted.PARTITION_NORMAL, parted.PARTITION_LOGICAL]:
                            size = partition.geometry.length * dev.sectorSize
                            size_gbytes = int(parted.formatBytes(size, 'GB'))
                            if size_gbytes > 0:
                                line = '\t{0} [{1} GB]'.format(
                                    partition.path, size_gbytes)
                                self.part_store.append_text(line)
                                self.devices_and_partitions[line] = (dev.path, partition.path)
                except parted._ped.DiskException as warn:
                    # It could be that the device has no partition table
                    logging.warning(warn)

        misc.select_first_combobox_item(self.part_store)

    def select_part_changed(self, _widget):
        """ User selected another drive """
        line = self.part_store.get_active_text()
        if line is not None:
            self.cache_path = self.devices_and_partitions[line]
        self.forward_button.set_sensitive(True)

    def prepare(self, direction):
        """ Get screen ready """
        self.translate_ui()
        self.populate_devices_and_partitions()
        self.umount_cache()
        self.show_all()

    def store_values(self):
        """ Continue """
        # Enable forward button
        self.forward_button.set_sensitive(True)
        return True

    def prepare_whole_device(self, device_path):
        """ Function that deletes device and creates a partition
        to be used as cache for xz packages """
        with misc.raised_privileges():
            dev = parted.getDevice(device_path)
            # Create a new device's partition table
            disk = parted.freshDisk(dev, 'gpt')
            geometry = parted.Geometry(start=0, length=dev.length, device=dev)
            new_partition = parted.Partition(
                disk=disk, type=parted.PARTITION_NORMAL, geometry=geometry)
            disk.addPartition(new_partition)

        txt = _("Device {} will be fully erased! Are you REALLY sure?").format(device_path)
        response = show.question(self.get_main_window(), txt)
        if response == Gtk.ResponseType.YES:
            with misc.raised_privileges():
                disk.commit()
            if len(disk.partitions) == 1:
                path = disk.partitions[0].path
                with misc.raised_privileges():
                    error, msg = fs.create_fs(path, "ext4")
                if error:
                    logging.error(msg)
                return path

        return None

    @staticmethod
    def umount_cache():
        """ Unmount cache directory (just in case) """
        if os.path.exists(Cache.CACHE_DIRECTORY):
            try:
                cmd = ["umount", Cache.CACHE_DIRECTORY]
                output = subprocess.check_output(
                    cmd, stdin=None,
                    stderr=subprocess.STDOUT,
                    timeout=None)
                output = output.decode()
                if output:
                    logging.debug(output.strip('\n'))
                os.rmdir(Cache.CACHE_DIRECTORY)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as msg:
                logging.debug(msg)

    def mount_cache(self, device_path, partition_path):
        """ Mounts cache partition into a folder
        If only a device is specified, cnchi will erase
        it and create a partition in it """
        if device_path:
            if partition_path is None:
                # Use the whole device as cache.
                partition_path = self.prepare_whole_device(device_path)
                if not partition_path:
                    return None
            mount_dir = Cache.CACHE_DIRECTORY
            if not os.path.exists(mount_dir):
                os.makedirs(mount_dir)
            if self.mount_partition(partition_path, mount_dir):
                logging.debug("%s partition mounted on %s to be used as xz cache",
                              partition_path, mount_dir)
                return mount_dir
            logging.warning("Could not mount %s in %s to be used as xz cache",
                            partition_path, mount_dir)
        return None

    @staticmethod
    def mount_partition(path, mount_dir):
        """ Mounts device path in mount_dir """
        try:
            cmd = ["mount", path, mount_dir]
            output = subprocess.check_output(
                cmd,
                stdin=None,
                stderr=subprocess.STDOUT,
                timeout=None)
            output = output.decode()
            if output:
                logging.debug(output.strip('\n'))
            update_db.sync()
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
            logging.error(err)
            return False

    def store_values(self):
        """ Store selected values """
        device_path, partition_path = self.cache_path
        if not device_path and not partition_path:
            # User does not want to use cache
            return True
        xz_cache_dir = self.mount_cache(device_path, partition_path)
        if xz_cache_dir:
            xz_cache = self.settings.get('xz_cache')
            if xz_cache_dir not in xz_cache:
                xz_cache.append(xz_cache_dir)
                self.settings.set('xz_cache', xz_cache)
                logging.debug("%s added to xz cache", xz_cache_dir)
                return True
        return False

# When testing, no _() is available
try:
    _("")
except NameError as err:
    def _(message):
        return message
