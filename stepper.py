"""
Lagniva-drivrutiner for stegmotorn samt MotorController som binder ihop
drivrutin, kalibrering och tillstand.

Tva drivrutiner finns:
- LgpioStepper    : verklig hardvara via lgpio (STEP+DIR till din driver,
                    hemsensor pa en GPIO-ingang).
- SimulatedStepper: mjukvarusimulering, anvands automatiskt om lgpio/
                    hardvaran inte kan initieras (t.ex. under
                    utveckling/test innan motorn ar inkopplad).
"""

import logging
import queue
import threading
import time

import config
import calibration

logger = logging.getLogger(__name__)


class MovementAborted(Exception):
    pass


class HomingError(Exception):
    pass


# ---------------------------------------------------------------------------
# Drivrutiner
# ---------------------------------------------------------------------------

class BaseStepper:
    def move_steps(self, n_steps, speed_sps, direction=1, abort_event=None, on_progress=None):
        raise NotImplementedError

    def read_home_sensor(self) -> bool:
        raise NotImplementedError

    def close(self):
        pass


class LgpioStepper(BaseStepper):
    def __init__(self):
        import lgpio
        self._lgpio = lgpio
        self.h = lgpio.gpiochip_open(config.GPIO_CHIP)
        lgpio.gpio_claim_output(self.h, config.STEP_PIN, 0)
        lgpio.gpio_claim_output(self.h, config.DIR_PIN, self._dir_level(1))

        pull_flag = 0
        if config.HOME_SENSOR_PULL == "up":
            pull_flag = lgpio.SET_PULL_UP
        elif config.HOME_SENSOR_PULL == "down":
            pull_flag = lgpio.SET_PULL_DOWN
        lgpio.gpio_claim_input(self.h, config.HOME_SENSOR_PIN, pull_flag)
        logger.info(
            "LgpioStepper redo (STEP=GPIO%d HOME=GPIO%d)",
            config.STEP_PIN, config.HOME_SENSOR_PIN,
        )

    @staticmethod
    def _dir_level(direction: int) -> int:
        """direction: 1 = framat, -1 = bakat. Ger GPIO-nivan for DIR_PIN,
        med hansyn till DIR_INVERT."""
        forward_level = 1 if config.DIR_INVERT else 0
        return forward_level if direction >= 0 else 1 - forward_level

    def move_steps(self, n_steps, speed_sps, direction=1, abort_event=None, on_progress=None):
        if n_steps <= 0:
            return
        lgpio = self._lgpio
        lgpio.gpio_write(self.h, config.DIR_PIN, self._dir_level(direction))
        period_us = 1_000_000.0 / float(speed_sps)
        half = max(1, int(period_us / 2))
        total_time = (half * 2 * n_steps) / 1_000_000.0

        lgpio.tx_pulse(self.h, config.STEP_PIN, half, half, 0, n_steps)
        start = time.time()
        while lgpio.tx_busy(self.h, config.STEP_PIN, lgpio.TX_PWM):
            if abort_event is not None and abort_event.is_set():
                lgpio.tx_pulse(self.h, config.STEP_PIN, 0, 0)  # stoppa direkt
                raise MovementAborted()
            if on_progress is not None:
                elapsed = time.time() - start
                done = min(n_steps, elapsed / total_time * n_steps if total_time > 0 else n_steps)
                on_progress(done)
            time.sleep(0.01)
        if on_progress is not None:
            on_progress(n_steps)

    def read_home_sensor(self) -> bool:
        level = self._lgpio.gpio_read(self.h, config.HOME_SENSOR_PIN)
        return (level == 0) if config.HOME_SENSOR_ACTIVE_LOW else (level == 1)

    def close(self):
        try:
            try:
                self._lgpio.tx_pulse(self.h, config.STEP_PIN, 0, 0)
            except self._lgpio.error:
                pass  # ingen pulstrain var igang - inget att stoppa
            self._lgpio.gpio_free(self.h, config.STEP_PIN)
            self._lgpio.gpio_free(self.h, config.DIR_PIN)
            self._lgpio.gpio_free(self.h, config.HOME_SENSOR_PIN)
            self._lgpio.gpiochip_close(self.h)
        except Exception:
            logger.exception("Fel vid nedstangning av GPIO")


class SimulatedStepper(BaseStepper):
    """Later hjulet snurra i (ungefar) verklig tid utan hardvara. Startar
    pa ett godtyckligt icke-hemma-lage sa hemkorning gar att testa.

    Hemsensorn simuleras med en liten fysisk bredd (precis som en riktig
    brytare/flagga) istallet for en enda exakt punkt - annars kan en
    grov sokning med stegvis chunkning missa en punktsensor helt om
    stegstorleken inte gar jamnt upp i avstandet till noll."""

    _HOME_SENSOR_WINDOW = 6  # steg at vardera hall om noll

    def __init__(self):
        self._pos = 500 % config.STEPS_PER_REV
        self._lock = threading.Lock()
        logger.info("SimulatedStepper aktiv (ingen riktig hardvara anvands)")

    def move_steps(self, n_steps, speed_sps, direction=1, abort_event=None, on_progress=None):
        if n_steps <= 0:
            return
        sign = 1 if direction >= 0 else -1
        total_time = n_steps / float(speed_sps)
        start = time.time()
        done_steps = 0
        interval = 0.02
        while True:
            elapsed = time.time() - start
            if elapsed >= total_time:
                break
            if abort_event is not None and abort_event.is_set():
                frac = elapsed / total_time if total_time > 0 else 1.0
                moved = int(n_steps * frac) - done_steps
                with self._lock:
                    self._pos = (self._pos + sign * moved) % config.STEPS_PER_REV
                raise MovementAborted()
            if on_progress is not None:
                on_progress(min(n_steps, elapsed / total_time * n_steps))
            time.sleep(interval)
        with self._lock:
            self._pos = (self._pos + sign * n_steps) % config.STEPS_PER_REV
        if on_progress is not None:
            on_progress(n_steps)

    def read_home_sensor(self) -> bool:
        with self._lock:
            dist = min(self._pos, config.STEPS_PER_REV - self._pos)
            return dist <= self._HOME_SENSOR_WINDOW

    def close(self):
        pass


def create_driver():
    """Valjer verklig hardvara om mojligt, annars simulering."""
    if config.FORCE_SIMULATION:
        return SimulatedStepper(), True
    try:
        return LgpioStepper(), False
    except Exception as exc:
        logger.warning("Kunde inte initiera lgpio-hardvara (%s) - anvander simulering.", exc)
        return SimulatedStepper(), True


# ---------------------------------------------------------------------------
# MotorController - hog-nivalogik: homing, positionering, kalibrering
# ---------------------------------------------------------------------------

class MotorController:
    def __init__(self):
        self.driver, self.simulated = create_driver()
        self.calibration = calibration.load_calibration()

        self.current_step = None     # None = okant lage (ej hemkord)
        self.homed = False
        self.moving = False
        self.calibrating = False
        self.error = None
        self.active_position = None  # senast bekraftade position (1-9) eller None
        self.homing_progress = None  # antal steg sokta hittills under pagaende hemkorning, annars None

        self._abort_event = threading.Event()
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self.on_state_change = None  # satts av app.py: callable(state_dict)

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    # -- publikt API, anropas fran Flask/SocketIO- och serial-tradar --------

    def goto_position(self, position: int):
        self._enqueue(lambda: self._do_goto(position))

    def home(self):
        self._enqueue(self._do_home)

    def home_and_wait(self):
        """Kor hemkorning och blockerar tills den ar klar (lyckad eller
        misslyckad). Anvands vid uppstart sa RS232-kommandon inte kan
        komma in medan hjulets lage fortfarande ar okant."""
        done_event = threading.Event()

        def task():
            try:
                self._do_home()
            finally:
                done_event.set()

        self._enqueue(task)
        done_event.wait()

    def jog(self, delta_steps: int):
        self._enqueue(lambda: self._do_jog(delta_steps))

    def save_calibration_here(self, position: int):
        self._enqueue(lambda: self._do_save_calibration(position))

    def emergency_stop(self):
        self._abort_event.set()

    def get_state(self) -> dict:
        with self._lock:
            return {
                "current_step": self.current_step,
                "homed": self.homed,
                "moving": self.moving,
                "calibrating": self.calibrating,
                "active_position": self.active_position,
                "homing_progress": self.homing_progress,
                "error": self.error,
                "simulated": self.simulated,
                "calibration": dict(self.calibration),
                "steps_per_rev": config.STEPS_PER_REV,
                "num_positions": config.NUM_POSITIONS,
            }

    def shutdown(self):
        self.driver.close()

    # -- intern koforvaltning -------------------------------------------

    def _enqueue(self, fn):
        self._queue.put(fn)

    def _worker_loop(self):
        while True:
            fn = self._queue.get()
            self._abort_event.clear()
            try:
                fn()
            except MovementAborted:
                with self._lock:
                    self.moving = False
                    self.homed = False
                    self.current_step = None
                    self.active_position = None
                    self.homing_progress = None
                    self.error = "Nodstopp - kor hemkorning igen"
                self._notify()
            except HomingError as exc:
                with self._lock:
                    self.moving = False
                    self.homing_progress = None
                    self.error = str(exc)
                self._notify()
            except Exception:
                logger.exception("Ovantat fel i motor-jobb")
                with self._lock:
                    self.moving = False
                    self.homing_progress = None
                    self.error = "Internt fel, se logg"
                self._notify()

    def _notify(self):
        if self.on_state_change is not None:
            try:
                self.on_state_change(self.get_state())
            except Exception:
                logger.exception("Fel vid state-notifiering")

    def _update_active_position(self):
        """Satter active_position till narmaste kalibrerade lage om vi
        star tillrackligt nara det, annars None (mellanlage)."""
        if self.current_step is None:
            self.active_position = None
            return
        tolerance = 3  # steg
        spr = config.STEPS_PER_REV
        best_pos, best_dist = None, None
        for pos, step in self.calibration.items():
            dist = min((self.current_step - step) % spr, (step - self.current_step) % spr)
            if best_dist is None or dist < best_dist:
                best_pos, best_dist = pos, dist
        self.active_position = best_pos if best_dist is not None and best_dist <= tolerance else None

    # -- faktiska rorelsejobb (kors pa worker-tradan) ---------------------

    def _progress_cb(self, base_step, direction=1):
        def cb(done_steps):
            with self._lock:
                spr = config.STEPS_PER_REV
                self.current_step = int(base_step + direction * done_steps) % spr
                self._update_active_position()
            self._notify()
        return cb

    def _do_goto(self, position):
        with self._lock:
            if not self.homed or self.current_step is None:
                self.error = "Ej hemkord - tryck Hemkorning forst"
                self._notify()
                return
            if position not in self.calibration:
                self.error = f"Position {position} ar inte kalibrerad"
                self._notify()
                return
            target = self.calibration[position]
            start = self.current_step
            self.moving = True
            self.error = None
        self._notify()

        # Motorn saknar riktningsstyrning - kor alltid framat (okande
        # stegrakning), aldrig genvagen bakat runt hjulet.
        spr = config.STEPS_PER_REV
        n_steps = (target - start) % spr

        self.driver.move_steps(
            n_steps, config.RUN_SPEED_STEPS_PER_SEC,
            abort_event=self._abort_event,
            on_progress=self._progress_cb(start),
        )

        with self._lock:
            self.current_step = target % spr
            self.moving = False
            self._update_active_position()
        self._notify()

    def _do_jog(self, delta_steps):
        with self._lock:
            if self.current_step is None:
                self.error = "Ej hemkord - tryck Hemkorning forst"
                self._notify()
                return
            if delta_steps == 0:
                return
            start = self.current_step
            self.moving = True
            self.error = None
        self._notify()

        direction = 1 if delta_steps > 0 else -1
        n_steps = abs(delta_steps)
        self.driver.move_steps(
            n_steps, config.JOG_SPEED_STEPS_PER_SEC,
            direction=direction,
            abort_event=self._abort_event,
            on_progress=self._progress_cb(start, direction),
        )

        with self._lock:
            self.current_step = (start + direction * n_steps) % config.STEPS_PER_REV
            self.moving = False
            self._update_active_position()
        self._notify()

    def _do_save_calibration(self, position):
        with self._lock:
            if self.current_step is None:
                self.error = "Ej hemkord - kan inte spara kalibrering"
                self._notify()
                return
            self.calibration[position] = self.current_step
            self._update_active_position()
        calibration.save_calibration(self.calibration)
        self._notify()

    def _do_home(self):
        with self._lock:
            self.moving = True
            self.homed = False
            self.error = None
            self.calibrating = False
            self.homing_progress = 0
        self._notify()

        driver = self.driver
        try:
            # Hemkorningen kor alltid framat och backar aldrig undan sensorn
            # for en andra annalkning - for att traffa exakt samma stegvarde
            # vid varje hemkorning provas darfor sensorn efter varje enskilt
            # steg, hela vagen.
            moved = 0
            while not driver.read_home_sensor():
                driver.move_steps(1, config.HOMING_SPEED_STEPS_PER_SEC,
                                   abort_event=self._abort_event)
                moved += 1
                with self._lock:
                    self.homing_progress = moved
                self._notify()
                if moved > config.HOMING_MAX_STEPS:
                    raise HomingError("Hittade inte hemsensorn - kontrollera koppling")

            with self._lock:
                self.current_step = 0
                self.homed = True
                self.moving = False
                self.homing_progress = None
                self._update_active_position()
            self._notify()
        except MovementAborted:
            raise
        except HomingError:
            with self._lock:
                self.moving = False
            raise
