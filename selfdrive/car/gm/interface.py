#!/usr/bin/env python3
from cereal import car
from common.numpy_fast import interp
from selfdrive.config import Conversions as CV
from selfdrive.controls.lib.drive_helpers import create_event, EventTypes as ET
from selfdrive.controls.lib.vehicle_model import VehicleModel
from selfdrive.car.gm.values import DBC, CAR, ECU, ECU_FINGERPRINT, \
                                    SUPERCRUISE_CARS, AccState, FINGERPRINTS
from selfdrive.car.gm.carstate import CarState, CruiseButtons, get_powertrain_can_parser, get_chassis_can_parser
from selfdrive.car import STD_CARGO_KG, scale_rot_inertia, scale_tire_stiffness, is_ecu_disconnected, gen_empty_fingerprint
from selfdrive.car.interfaces import CarInterfaceBase

FOLLOW_AGGRESSION = 0.15 # (Acceleration/Decel aggression) Lower is more aggressive


ButtonType = car.CarState.ButtonEvent.Type

class CanBus(CarInterfaceBase):
  def __init__(self):
    self.powertrain = 0
    self.obstacle = 1
    self.chassis = 2
    self.sw_gmlan = 3

class CarInterface(CarInterfaceBase):
  def __init__(self, CP, CarController):
    self.CP = CP

    self.frame = 0
    self.gas_pressed_prev = False
    self.brake_pressed_prev = False
    self.acc_active_prev = 0

    # *** init the major players ***
    canbus = CanBus()
    self.CS = CarState(CP, canbus)
    self.VM = VehicleModel(CP)
    self.pt_cp = get_powertrain_can_parser(CP, canbus)
    self.ch_cp = get_chassis_can_parser(CP, canbus)
    self.ch_cp_dbc_name = DBC[CP.carFingerprint]['chassis']

    self.CC = None
    if CarController is not None:
      self.CC = CarController(canbus, CP.carFingerprint)

  @staticmethod
  def compute_gb(accel, speed):
  	# Ripped from compute_gb_honda in Honda's interface.py. Works well off shelf but may need more tuning
    creep_brake = 0.0
    creep_speed = 2.68
    creep_brake_value = 0.10
    if speed < creep_speed:
      creep_brake = (creep_speed - speed) / creep_speed * creep_brake_value
    return float(accel) / 4.8 - creep_brake

  @staticmethod
  
  
  def calc_accel_override(a_ego, a_target, v_ego, v_target):

    # normalized max accel. Allowing max accel at low speed causes speed overshoots
    max_accel_bp = [10, 20]    # m/s
    max_accel_v = [0.85, 1.0] # unit of max accel
    max_accel = interp(v_ego, max_accel_bp, max_accel_v)

    # limit the pcm accel cmd if:
    # - v_ego exceeds v_target, or
    # - a_ego exceeds a_target and v_ego is close to v_target

    eA = a_ego - a_target
    valuesA = [1.0, 0.1]
    bpA = [0.3, 1.1]

    eV = v_ego - v_target
    valuesV = [1.0, 0.1]
    bpV = [0.0, 0.5]

    valuesRangeV = [1., 0.]
    bpRangeV = [-1., 0.]

    # only limit if v_ego is close to v_target
    speedLimiter = interp(eV, bpV, valuesV)
    accelLimiter = max(interp(eA, bpA, valuesA), interp(eV, bpRangeV, valuesRangeV))

    # accelOverride is more or less the max throttle allowed to pcm: usually set to a constant
    # unless aTargetMax is very high and then we scale with it; this help in quicker restart

    return float(max(max_accel, a_target / FOLLOW_AGGRESSION)) * min(speedLimiter, accelLimiter)

  @staticmethod
  def get_params(candidate, fingerprint=gen_empty_fingerprint(), has_relay=False, car_fw=[]):
    ret = car.CarParams.new_message()

    ret.carName = "gm"
    ret.carFingerprint = candidate
    ret.isPandaBlack = has_relay

    ret.enableCruise = False
    # GM port is considered a community feature, since it disables AEB;
    # TODO: make a port that uses a car harness and it only intercepts the camera
    ret.communityFeature = True

    # Presence of a camera on the object bus is ok.
    # Have to go to read_only if ASCM is online (ACC-enabled cars),
    # or camera is on powertrain bus (LKA cars without ACC).
    ret.enableCamera = is_ecu_disconnected(fingerprint[0], FINGERPRINTS, ECU_FINGERPRINT, candidate, ECU.CAM) or \
                       has_relay or \
                       candidate == CAR.CADILLAC_CT6
    ret.openpilotLongitudinalControl = ret.enableCamera
    tire_stiffness_factor = 0.444  # not optimized yet


    # same tuning for Volt and CT6 for now
    ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
    ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.2], [0.00]]
    ret.lateralTuning.pid.kf = 0.00004   # full torque for 20 deg at 80mph means 0.00007818594
    ret.steerRateCost = 1.0

    if candidate == CAR.VOLT:
      # supports stop and go, but initial engage must be above 18mph (which include conservatism)
      ret.minEnableSpeed = 8 * CV.MPH_TO_MS
      ret.mass = 1607. + STD_CARGO_KG
      ret.safetyModel = car.CarParams.SafetyModel.gm
      ret.wheelbase = 2.69
      ret.steerRatio = 15.7
      ret.steerRatioRear = 0.
      ret.centerToFront = ret.wheelbase * 0.4 # wild guess
      ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.12], [0.05]]
      ret.steerRateCost = 0.7

    elif candidate == CAR.MALIBU:
      # supports stop and go, but initial engage must be above 18mph (which include conservatism)
      ret.minEnableSpeed = 18 * CV.MPH_TO_MS
      ret.mass = 1496. + STD_CARGO_KG
      ret.safetyModel = car.CarParams.SafetyModel.gm
      ret.wheelbase = 2.83
      ret.steerRatio = 15.8
      ret.steerRatioRear = 0.
      ret.centerToFront = ret.wheelbase * 0.4 # wild guess

    elif candidate == CAR.HOLDEN_ASTRA:
      ret.mass = 1363. + STD_CARGO_KG
      ret.wheelbase = 2.662
      # Remaining parameters copied from Volt for now
      ret.centerToFront = ret.wheelbase * 0.4
      ret.minEnableSpeed = 18 * CV.MPH_TO_MS
      ret.safetyModel = car.CarParams.SafetyModel.gm
      ret.steerRatio = 15.7
      ret.steerRatioRear = 0.

    elif candidate == CAR.ACADIA:
      ret.minEnableSpeed = -1. # engage speed is decided by pcm
      ret.mass = 4353. * CV.LB_TO_KG + STD_CARGO_KG
      ret.safetyModel = car.CarParams.SafetyModel.gm
      ret.wheelbase = 2.86
      ret.steerRatio = 14.4  #end to end is 13.46
      ret.steerRatioRear = 0.
      ret.centerToFront = ret.wheelbase * 0.4

    elif candidate == CAR.BUICK_REGAL:
      ret.minEnableSpeed = 18 * CV.MPH_TO_MS
      ret.mass = 3779. * CV.LB_TO_KG + STD_CARGO_KG # (3849+3708)/2
      ret.safetyModel = car.CarParams.SafetyModel.gm
      ret.wheelbase = 2.83 #111.4 inches in meters
      ret.steerRatio = 14.4 # guess for tourx
      ret.steerRatioRear = 0.
      ret.centerToFront = ret.wheelbase * 0.4 # guess for tourx

    elif candidate == CAR.CADILLAC_ATS:
      ret.minEnableSpeed = 18 * CV.MPH_TO_MS
      ret.mass = 1601. + STD_CARGO_KG
      ret.safetyModel = car.CarParams.SafetyModel.gm
      ret.wheelbase = 2.78
      ret.steerRatio = 15.3
      ret.steerRatioRear = 0.
      ret.centerToFront = ret.wheelbase * 0.49

    elif candidate == CAR.CADILLAC_CT6:
      # engage speed is decided by pcm
      ret.minEnableSpeed = -1.
      ret.mass = 4016. * CV.LB_TO_KG + STD_CARGO_KG
      ret.safetyModel = car.CarParams.SafetyModel.cadillac
      ret.wheelbase = 3.11
      ret.steerRatio = 14.6   # it's 16.3 without rear active steering
      ret.steerRatioRear = 0. # TODO: there is RAS on this car!
      ret.centerToFront = ret.wheelbase * 0.465


    # TODO: get actual value, for now starting with reasonable value for
    # civic and scaling by mass and wheelbase
    ret.rotationalInertia = scale_rot_inertia(ret.mass, ret.wheelbase)

    # TODO: start from empirically derived lateral slip stiffness for the civic and scale by
    # mass and CG position, so all cars will have approximately similar dyn behaviors
    ret.tireStiffnessFront, ret.tireStiffnessRear = scale_tire_stiffness(ret.mass, ret.wheelbase, ret.centerToFront,
                                                                         tire_stiffness_factor=tire_stiffness_factor)

    ret.steerMaxBP = [0.]  # m/s
    ret.steerMaxV = [1.]
    ret.gasMaxBP = [0.]
    ret.gasMaxV = [0.5]
    ret.brakeMaxBP = [0.]
    ret.brakeMaxV = [1.]

    ret.longitudinalTuning.kpBP = [5., 35.]
    ret.longitudinalTuning.kpV = [2.4, 1.5]
    ret.longitudinalTuning.kiBP = [0.]
    ret.longitudinalTuning.kiV = [0.36]
    ret.longitudinalTuning.deadzoneBP = [0.]
    ret.longitudinalTuning.deadzoneV = [0.]

    ret.stoppingControl = True
    ret.startAccel = 0.8

    ret.steerActuatorDelay = 0.1  # Default delay, not measured yet
    ret.steerLimitTimer = 0.4
    ret.radarTimeStep = 0.0667  # GM radar runs at 15Hz instead of standard 20Hz
    ret.steerControlType = car.CarParams.SteerControlType.torque

    return ret

  # returns a car.CarState
  def update(self, c, can_strings):
    self.pt_cp.update_strings(can_strings)
    self.ch_cp.update_strings(can_strings)
    self.CS.update(self.pt_cp, self.ch_cp)

    # create message
    ret = car.CarState.new_message()

    ret.canValid = self.pt_cp.can_valid

    # speeds
    ret.vEgo = self.CS.v_ego
    ret.aEgo = self.CS.a_ego
    ret.vEgoRaw = self.CS.v_ego_raw
    ret.yawRate = self.VM.yaw_rate(self.CS.angle_steers * CV.DEG_TO_RAD, self.CS.v_ego)
    ret.standstill = self.CS.standstill
    ret.wheelSpeeds.fl = self.CS.v_wheel_fl
    ret.wheelSpeeds.fr = self.CS.v_wheel_fr
    ret.wheelSpeeds.rl = self.CS.v_wheel_rl
    ret.wheelSpeeds.rr = self.CS.v_wheel_rr

    # gas pedal information.
    ret.gas = self.CS.pedal_gas / 254.0
    ret.gasPressed = self.CS.user_gas_pressed

    # brake pedal
    ret.brake = self.CS.user_brake / 0xd0
    ret.brakePressed = self.CS.brake_pressed
    ret.brakeLights = self.CS.frictionBrakesActive
    
    # steering wheel
    ret.steeringAngle = self.CS.angle_steers

    # torque and user override. Driver awareness
    # timer resets when the user uses the steering wheel.
    ret.steeringPressed = self.CS.steer_override
    ret.steeringTorque = self.CS.steer_torque_driver
    ret.steeringRateLimited = self.CC.steer_rate_limited if self.CC is not None else False

    # cruise state
    ret.cruiseState.available = bool(self.CS.main_on)
    cruiseEnabled = self.CS.pcm_acc_status != AccState.OFF
    ret.cruiseState.enabled = cruiseEnabled
    ret.cruiseState.standstill = False

    ret.leftBlinker = self.CS.left_blinker_on
    ret.rightBlinker = self.CS.right_blinker_on
    ret.doorOpen = not self.CS.door_all_closed
    ret.seatbeltUnlatched = not self.CS.seatbelt
    ret.gearShifter = self.CS.gear_shifter
    ret.readdistancelines = self.CS.follow_level

    buttonEvents = []

    # blinkers
    if self.CS.left_blinker_on != self.CS.prev_left_blinker_on:
      be = car.CarState.ButtonEvent.new_message()
      be.type = ButtonType.leftBlinker
      be.pressed = self.CS.left_blinker_on
      buttonEvents.append(be)

    if self.CS.right_blinker_on != self.CS.prev_right_blinker_on:
      be = car.CarState.ButtonEvent.new_message()
      be.type = ButtonType.rightBlinker
      be.pressed = self.CS.right_blinker_on
      buttonEvents.append(be)

    if self.CS.cruise_buttons != self.CS.prev_cruise_buttons:
      be = car.CarState.ButtonEvent.new_message()
      be.type = ButtonType.unknown
      if self.CS.cruise_buttons != CruiseButtons.UNPRESS:
        be.pressed = True
        but = self.CS.cruise_buttons
      else:
        be.pressed = False
        but = self.CS.prev_cruise_buttons
      if but == CruiseButtons.RES_ACCEL:
        if not (cruiseEnabled and self.CS.standstill):
          be.type = ButtonType.accelCruise # Suppress resume button if we're resuming from stop so we don't adjust speed.
      elif but == CruiseButtons.DECEL_SET:
        if not cruiseEnabled and not self.CS.lkMode:
          self.lkMode = True
        be.type = ButtonType.decelCruise
      elif but == CruiseButtons.CANCEL:
        be.type = ButtonType.cancel
      elif but == CruiseButtons.MAIN:
        be.type = ButtonType.altButton3
      buttonEvents.append(be)

    ret.buttonEvents = buttonEvents
    
    if cruiseEnabled and self.CS.lka_button and self.CS.lka_button != self.CS.prev_lka_button:
      self.CS.lkMode = not self.CS.lkMode

    if self.CS.distance_button and self.CS.distance_button != self.CS.prev_distance_button:
       self.CS.follow_level -= 1
       if self.CS.follow_level < 1:
         self.CS.follow_level = 3

    events = []
    
    if not self.CS.lkMode:
      events.append(create_event('manualSteeringRequired', [ET.WARNING]))
    #if cruiseEnabled and (self.CS.left_blinker_on or self.CS.right_blinker_on):
    #   events.append(create_event('manualSteeringRequiredBlinkersOn', [ET.WARNING]))

    if self.CS.steer_error:
      events.append(create_event('steerUnavailable', [ET.NO_ENTRY, ET.IMMEDIATE_DISABLE, ET.PERMANENT]))
    if self.CS.steer_not_allowed:
      events.append(create_event('steerTempUnavailable', [ET.NO_ENTRY, ET.WARNING]))
    if ret.doorOpen:
      events.append(create_event('doorOpen', [ET.NO_ENTRY, ET.SOFT_DISABLE]))
    if ret.seatbeltUnlatched:
      events.append(create_event('seatbeltNotLatched', [ET.NO_ENTRY, ET.SOFT_DISABLE]))

    if self.CS.car_fingerprint in SUPERCRUISE_CARS:
      if self.CS.acc_active and not self.acc_active_prev:
        events.append(create_event('pcmEnable', [ET.ENABLE]))
      if not self.CS.acc_active:
        events.append(create_event('pcmDisable', [ET.USER_DISABLE]))

    else:
      if self.CS.brake_error:
        events.append(create_event('brakeUnavailable', [ET.NO_ENTRY, ET.IMMEDIATE_DISABLE, ET.PERMANENT]))
      if not self.CS.gear_shifter_valid:
        events.append(create_event('wrongGear', [ET.NO_ENTRY, ET.SOFT_DISABLE]))
      if self.CS.esp_disabled:
        events.append(create_event('espDisabled', [ET.NO_ENTRY, ET.SOFT_DISABLE]))
      if not self.CS.main_on:
        events.append(create_event('wrongCarMode', [ET.NO_ENTRY, ET.USER_DISABLE]))
      if self.CS.gear_shifter == 3:
        events.append(create_event('reverseGear', [ET.NO_ENTRY, ET.IMMEDIATE_DISABLE]))
      if ret.vEgo < self.CP.minEnableSpeed:
        events.append(create_event('speedTooLow', [ET.NO_ENTRY]))
      if self.CS.park_brake:
        events.append(create_event('parkBrake', [ET.NO_ENTRY, ET.USER_DISABLE]))
      # disable on pedals rising edge or when brake is pressed and speed isn't zero
      if (ret.gasPressed and not self.gas_pressed_prev) or \
        (ret.brakePressed): # and (not self.brake_pressed_prev or ret.vEgo > 0.001)):
        events.append(create_event('pedalPressed', [ET.NO_ENTRY, ET.USER_DISABLE]))
      if ret.gasPressed:
        events.append(create_event('pedalPressed', [ET.PRE_ENABLE]))
      if ret.cruiseState.standstill:
        events.append(create_event('resumeRequired', [ET.WARNING]))
      #if self.CS.pcm_acc_status == AccState.FAULTED:
       # events.append(create_event('controlsFailed', [ET.NO_ENTRY, ET.IMMEDIATE_DISABLE]))

      # handle button presses
      for b in ret.buttonEvents:
        # do enable on both accel and decel buttons
        # The ECM will fault if resume triggers an enable while speed is set to 0
        if b.type == ButtonType.accelCruise and c.hudControl.setSpeed > 0 and c.hudControl.setSpeed < 70 and not b.pressed:
          events.append(create_event('buttonEnable', [ET.ENABLE]))
        if b.type == ButtonType.decelCruise and not b.pressed:
          events.append(create_event('buttonEnable', [ET.ENABLE]))
        # do disable on button down
        if b.type == ButtonType.cancel and b.pressed:
          events.append(create_event('buttonCancel', [ET.USER_DISABLE]))
        # The ECM independently tracks a ‘speed is set’ state that is reset on main off.
        # To keep controlsd in sync with the ECM state, generate a RESET_V_CRUISE event on main cruise presses.
        if b.type == ButtonType.altButton3 and b.pressed:
          events.append(create_event('buttonCancel', [ET.RESET_V_CRUISE, ET.USER_DISABLE]))

    ret.events = events

    # update previous brake/gas pressed
    self.acc_active_prev = self.CS.acc_active
    self.gas_pressed_prev = ret.gasPressed
    self.brake_pressed_prev = ret.brakePressed

    # cast to reader so it can't be modified
    return ret.as_reader()

  # pass in a car.CarControl
  # to be called @ 100hz
  def apply(self, c):
    hud_v_cruise = c.hudControl.setSpeed
    if hud_v_cruise > 70:
      hud_v_cruise = 0

    # For Openpilot, "enabled" includes pre-enable.
    # In GM, PCM faults out if ACC command overlaps user gas.
    enabled = c.enabled and not self.CS.user_gas_pressed

    can_sends = self.CC.update(enabled, self.CS, self.frame, \
                               c.actuators,
                               hud_v_cruise, c.hudControl.lanesVisible, \
                               c.hudControl.leadVisible, c.hudControl.visualAlert)

    self.frame += 1
    return can_sends
