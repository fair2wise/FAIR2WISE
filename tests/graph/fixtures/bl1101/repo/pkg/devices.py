"""Synthetic device/plan module for the bl1101 symbol-extraction golden test."""
from ophyd import Component, Device, EpicsMotor


class SimMotor(Device):
    value = Component(EpicsMotor, "x")


class AreaDetectorCCD:
    pass


class SampleEndstation(Device):
    pass


class Endstation(Device):
    pass


class ScanPlan:
    pass


class BadError(Device):
    pass


class Helper:
    pass


@parameter_annotation_decorator
def rsoxs_run(energy: float):
    yield from []


def rel_scan(detectors, motor):
    yield from []


def count(detectors):
    yield from []


def _private_scan():
    yield from []


def load_sim_devices(path):
    return path


def unrelated():
    return 1
