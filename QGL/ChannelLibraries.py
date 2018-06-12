'''
Channels is where we store information for mapping virtual (qubit) channel to
real channels.

Split from Channels.py on Jan 14, 2016.
Moved to pony ORM from atom June 1, 2018

Original Author: Colm Ryan
Modified By: Graham Rowlands

Copyright 2016 Raytheon BBN Technologies

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Include modification to yaml loader (MIT License) from
https://gist.github.com/joshbode/569627ced3076931b02f

Scientific notation fix for yaml from
https://stackoverflow.com/questions/30458977/yaml-loads-5e-6-as-string-and-not-a-number
'''

import sys
import os
import re
import datetime
import traceback
import datetime
import importlib
import inspect
from pony.orm import *
import networkx as nx

from . import config
from . import Channels
from . import PulseShapes

channelLib = None

def set_from_dict(obj, settings):
    for prop_name in obj.to_dict().keys():
        if prop_name in settings.keys():
            try:
                setattr(obj, prop_name, settings[prop_name])
            except Exception as e:
                print(f"{obj.label}: Error loading {prop_name} from config")

def copy_objs(chans, srcs, new_channel_db):
    new_chans       = []
    new_srcs        = []
    old_to_new_chan = {}
    old_to_new_src  = {}

    for chan in chans:
        c = copy_entity(chan, new_channel_db)
        new_chans.append(c)
        old_to_new_chan[chan] = c

    for src in srcs:
        c = copy_entity(src, new_channel_db)
        new_srcs.append(c)
        old_to_new_src[src] = c

    # Fix links... pony updates the relationships symmetriacally so we get some for free
    for thing in new_chans + new_srcs:
        for attr in thing._attrs_:
            if attr:
                if isinstance(getattr(thing, attr.name), Channels.Channel):
                    if getattr(thing, attr.name) in old_to_new_chan.keys():
                        setattr(thing, attr.name, old_to_new_chan[getattr(thing, attr.name)])
                elif isinstance(getattr(thing, attr.name), Channels.MicrowaveSource):
                    if getattr(thing, attr.name) in old_to_new_src.keys():
                        setattr(thing, attr.name, old_to_new_src[getattr(thing, attr.name)])

    return new_chans, new_srcs

def copy_entity(obj, new_channel_db):
    """Copy a pony entity instance"""
    kwargs = {a.name: getattr(obj, a.name) for a in obj.__class__._attrs_ if a.name not in ["id", "classtype", "pulse_params"]}
    # if "pulse_params" in kwargs.keys():
    #     kwargs["pulse_params"] = dict(kwargs["pulse_params"])
    kwargs["channel_db"] = new_channel_db
    return obj.__class__(**kwargs)

# def copy_entity(obj, new_channel_db):
#     """Copy a pony entity instance"""
#     attr_names = [a.name for a in obj.__class__._attrs_]
#     skip = ["id", "classtype", "channel_db"]
#     print(obj)
#     kwargs = {"channel_db": new_channel_db}
#     for a in attr_names: #["label", "source_type"]:
#         print("\t", a)
#         if a in dir(obj):
#             val = getattr(obj, a)
#             if a == "pulse_params":
#                 val = dict(val)
#             if val is not None and a not in skip:
#                 print("\t\t", getattr(obj, a))
#                 kwargs[a] = val
#     return obj.__class__(**kwargs)

class ChannelLibrary(object):

    def __init__(self, channel_db_name=None, database_file=None, channelDict={}, **kwargs):
        """Create the channel library."""

        global channelLib
        if channelLib is not None:
            channelLib.db.disconnect()

        config.load_db()
        if database_file:
            self.database_file = database_file
        elif config.db_file:
            self.database_file = config.db_file
        else:
            self.database_file = ":memory:"

        self.db = Database()
        Channels.define_entities(self.db)
        self.db.bind('sqlite', filename=self.database_file, create_db=True)
        self.db.generate_mapping(create_tables=True)

        # Dirty trick: push the correct entity defs to the calling context
        for var in ["Measurement","Qubit","Edge"]:
            inspect.stack()[1][0].f_globals[var] = getattr(Channels, var)

        self.connectivityG = nx.DiGraph()
        
        # This is still somewhere legacy QGL behavior. Massage db into dict for lookup.
        self.channelDict = {}
        self.channels = []
        self.sources = []
        self.channelDatabase = Channels.ChannelDatabase(label="__temp__", time=datetime.datetime.now())
        self.channel_db_name = channel_db_name if channel_db_name else "temp"

        config.load_config()

        # self.load_most_recent()
        # config.load_config()

        # Update the global reference
        channelLib = self

    def get_current_channels(self):
        return list(select(c for c in Channels.Channel if c.channel_db is None)) + list(select(c for c in Channels.MicrowaveSource if c.channel_db is None))

    def update_channelDict(self):
        self.channelDict = {c.label: c for c in self.get_current_channels()}

    def list(self):
        select((c.label, c.time, c.id) for c in Channels.ChannelDatabase).show()

    def load_by_id(self, id_num):
        obj = select(c for c in Channels.ChannelDatabase if c.id==id_num).first()
        self.load(obj)

    def clear(self):
        select(c for c in Channels.Channel if c.channel_db == self.channelDatabase).delete(bulk=True)
        select(c for c in Channels.MicrowaveSource if c.channel_db == self.channelDatabase).delete(bulk=True)
        self.channelDatabase.time  = datetime.datetime.now()

    def load(self, obj): #, delete=True):
        self.clear()

        chans = list(obj.channels)
        srcs  = list(obj.sources)

        # self.channelDatabase = Channels.ChannelDatabase(label="__temp__", time=datetime.datetime.now())
        new_chans, new_srcs = copy_objs(chans, srcs, self.channelDatabase)

        self.channels = new_chans
        self.sources = new_srcs
        self.channel_db_name = obj.label
        # self.channelDatabase = None

    # def load_most_recent(self, name=None):
    #     if name is None:
    #         name = self.channel_db_name
    #     mrcd = Channels.ChannelDatabase.select(lambda d: d.label==name).order_by(desc(Channels.ChannelDatabase.time)).first()
    #     if mrcd:
    #         self.load(mrcd)

    # def new(self, name):
    #     # self.channelDatabase.delete()
    #     self.clear()
    #     commit()

    #     # self.channelDatabase = Channels.ChannelDatabase(label="__temp__", time=datetime.datetime.now())

    #     # self.channelDatabase = None
    #     self.channel_db_name = name
    #     self.channels = []
    #     self.sources = []

    def save(self):
        self.save_as(self.channel_db_name)

    def save_as(self, name):
        # self.channelDatabase.label = name
        # self.channelDatabase.time  = datetime.datetime.now()
        # cd = self.channelDatabase
        # self.channelDatabase = None
        # commit()
        # self.load(cd, delete=False)
        
        # Get channels that are part of the currently active db
        # chans = list(select(c for c in Channels.Channel if c.channel_db is None))
        # srcs  = list(select(c for c in Channels.MicrowaveSource if c.channel_db is None))
        chans = list(self.channelDatabase.channels)
        srcs  = list(self.channelDatabase.sources)
        cd    = Channels.ChannelDatabase(label=name, time=datetime.datetime.now(), channels=chans, sources=srcs)
        new_chans, new_srcs = copy_objs(chans, srcs, cd)

        # self.channels = new_chans
        # self.sources = new_srcs
        # self.channelDatabase = None
        commit()
        # self.channel_db_name = name

    #Dictionary methods
    def __getitem__(self, key):
        return self.channelDict[key]

    def __setitem__(self, key, value):
        self.channelDict[key] = value

    def __delitem__(self, key):
        del self.channelDict[key]

    def __contains__(self, key):
        return key in self.channelDict

    def keys(self):
        return self.channelDict.keys()

    def values(self):
        return self.channelDict.values()

    def build_connectivity_graph(self):
        # build connectivity graph
        for chan in select(q for q in Channels.Qubit if q not in self.connectivityG):
            self.connectivityG.add_node(chan)
        for chan in select(e for e in Channels.Edge):
            self.connectivityG.add_edge(chan.source, chan.target)
            self.connectivityG[chan.source][chan.target]['channel'] = chan

# Convenience functions for generating and linking channels
# TODO: move these to a shim layer shared by Auspex/QGL

class APS2(object):
    def __init__(self, label, address=None, delay=0.0):
        self.chan12 = Channels.PhysicalQuadratureChannel(label=f"{label}-12", instrument=label, translator="APS2Pattern", channel_db=channelLib.channelDatabase)
        self.m1     = Channels.PhysicalMarkerChannel(label=f"{label}-12m1", instrument=label, translator="APS2Pattern", channel_db=channelLib.channelDatabase)
        self.m2     = Channels.PhysicalMarkerChannel(label=f"{label}-12m2", instrument=label, translator="APS2Pattern", channel_db=channelLib.channelDatabase)
        self.m3     = Channels.PhysicalMarkerChannel(label=f"{label}-12m3", instrument=label, translator="APS2Pattern", channel_db=channelLib.channelDatabase)
        self.m4     = Channels.PhysicalMarkerChannel(label=f"{label}-12m4", instrument=label, translator="APS2Pattern", channel_db=channelLib.channelDatabase)
        
        self.trigger_interval = None
        self.trigger_source   = "External"
        self.address          = address
        self.delay            = delay
        self.master           = False

class X6(object):
    def __init__(self, label, address=None):
        self.chan1 = Channels.ReceiverChannel(label=f"RecvChan-{label}-1", channel_db=channelLib.channelDatabase)
        self.chan2 = Channels.ReceiverChannel(label=f"RecvChan-{label}-2", channel_db=channelLib.channelDatabase)
        
        self.address          = address
        self.reference        = "external"
        self.nbr_segments     = 1
        self.nbr_round_robins = 100
        self.acquire_mode     = "digitizer"

def new_qubit(label):
    return Channels.Qubit(label=label, channel_db=channelLib.channelDatabase)

def new_source(label, source_type, address, power=-30.0):
    return Channels.MicrowaveSource(label=label, source_type=source_type, address=address, power=power, channel_db=channelLib.channelDatabase)

def set_control(qubit, awg, generator=None):
    qubit.phys_chan = awg.chan12
    if generator:
        qubit.phys_chan.generator = generator
    
def set_measure(qubit, awg, dig, generator=None, dig_channel=1, trig_channel=1, gate=False, gate_channel=2, trigger_length=1e-7):
    meas = Channels.Measurement(label=f"M-{qubit.label}", channel_db=channelLib.channelDatabase)
    meas.phys_chan     = awg.chan12
    
    meas.trig_chan              = Channels.LogicalMarkerChannel(label=f"digTrig-{qubit.label}", channel_db=channelLib.channelDatabase)
    meas.trig_chan.phys_chan    = getattr(awg, f"m{trig_channel}")
    meas.trig_chan.pulse_params = {"length": trigger_length, "shape_fun": "constant"}
    meas.receiver_chan          = getattr(dig, f"chan{dig_channel}")

    if generator:
        meas.phys_chan.generator = generator

    if gate:
        meas.gate_chan           = Channels.LogicalMarkerChannel(label=f"M-{qubit.label}-gate", channel_db=channelLib.channelDatabase)
        meas.gate_chan.phys_chan = getattr(awg, f"m{gate_channel}")
        
def set_master(awg, trig_channel=2, pulse_length=1e-7):
    st = Channels.LogicalMarkerChannel(label="slave_trig", channel_db=channelLib.channelDatabase)
    st.phys_chan = getattr(awg, f"m{trig_channel}")
    st.pulse_params = {"length": pulse_length, "shape_fun": "constant"}
    awg.master = True
    awg.trigger_source = "Internal"

def QubitFactory(label, **kwargs):
    ''' Return a saved qubit channel or create a new one. '''
    # TODO: this will just get the first entry in the whole damned DB!
    # thing = select(el for el in Channels.Qubit if el.label==label).first()
    thing = {c.label: c for c in channelLib.get_current_channels()}[label]
    if thing:
        return thing
    else:
        return Channels.Qubit(label=label, **kwargs)
    
def MeasFactory(label, **kwargs):
    ''' Return a saved measurement channel or create a new one. '''
    thing = {c.label: c for c in channelLib.get_current_channels()}[label]
    if thing:
        return thing
    else:
        return Channels.Measurement(label=label, **kwargs)

def MarkerFactory(label, **kwargs):
    ''' Return a saved Marker channel or create a new one. '''
    thing = {c.label: c for c in channelLib.get_current_channels()}[label]
    if thing:
        return thing
    else:
        return Channels.LogicalMarkerChannel(label=label, **kwargs)

def EdgeFactory(source, target):
    if channelLib.connectivityG.has_edge(source, target):
        return channelLib.connectivityG[source][target]['channel']
    elif channelLib.connectivityG.has_edge(target, source):
        return channelLib.connectivityG[target][source]['channel']
    else:
        raise ValueError('Edge {0} not found in connectivity graph'.format((
            source, target)))

