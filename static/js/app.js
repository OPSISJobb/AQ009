(function () {
  "use strict";

  const socket = io();

  const statusPill = document.getElementById("statusPill");
  const simBadge = document.getElementById("simBadge");
  const stepReadout = document.getElementById("stepReadout");
  const errorBanner = document.getElementById("errorBanner");
  const cylinderGroup = document.getElementById("cylinderGroup");
  const positionGrid = document.getElementById("positionGrid");
  const saveGrid = document.getElementById("saveGrid");
  const homeBtn = document.getElementById("homeBtn");
  const stopBtn = document.getElementById("stopBtn");
  const calibrateToggleBtn = document.getElementById("calibrateToggleBtn");
  const closeCalibrationBtn = document.getElementById("closeCalibrationBtn");
  const calibrationPanel = document.getElementById("calibrationPanel");

  let lastState = null;
  let numPositions = 9;
  let chamberEls = {};   // position -> {group, circle}
  let saveBtnEls = {};   // position -> {btn, stepVal}
  let posBtnEls = {};    // position -> button

  const CHAMBER_RADIUS = 100;
  const CHAMBER_R = 34;

  function buildStaticElements(n) {
    numPositions = n;

    cylinderGroup.innerHTML = "";
    chamberEls = {};
    for (let i = 1; i <= n; i++) {
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", "chamber-static");

      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("r", CHAMBER_R);
      circle.setAttribute("class", "chamber");

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "chamber-label");
      label.textContent = String(i);

      g.appendChild(circle);
      g.appendChild(label);
      cylinderGroup.appendChild(g);
      chamberEls[i] = { g, circle, label };
    }

    positionGrid.innerHTML = "";
    posBtnEls = {};
    for (let i = 1; i <= n; i++) {
      const btn = document.createElement("button");
      btn.className = "pos-btn";
      btn.textContent = String(i);
      btn.addEventListener("click", () => socket.emit("goto", { position: i }));
      positionGrid.appendChild(btn);
      posBtnEls[i] = btn;
    }

    saveGrid.innerHTML = "";
    saveBtnEls = {};
    for (let i = 1; i <= n; i++) {
      const btn = document.createElement("button");
      btn.className = "save-btn";
      const label = document.createElement("span");
      label.textContent = "Spara " + i;
      const stepVal = document.createElement("span");
      stepVal.className = "step-val";
      btn.appendChild(label);
      btn.appendChild(stepVal);
      btn.addEventListener("click", () => socket.emit("save_calibration", { position: i }));
      saveGrid.appendChild(btn);
      saveBtnEls[i] = { btn, stepVal };
    }
  }

  function placeChambers(calibration, stepsPerRev) {
    for (const posStr of Object.keys(calibration)) {
      const pos = parseInt(posStr, 10);
      const el = chamberEls[pos];
      if (!el) continue;
      const angleDeg = (calibration[posStr] / stepsPerRev) * 360;
      const rad = (angleDeg * Math.PI) / 180;
      const x = CHAMBER_RADIUS * Math.sin(rad);
      const y = -CHAMBER_RADIUS * Math.cos(rad);
      el.g.setAttribute("transform", `translate(${x.toFixed(2)},${y.toFixed(2)})`);
    }
  }

  function render(state) {
    lastState = state;

    if (Object.keys(chamberEls).length !== state.num_positions) {
      buildStaticElements(state.num_positions);
    }
    placeChambers(state.calibration, state.steps_per_rev);

    // rotera cylindern sa aktuellt steg hamnar under pekaren
    if (state.current_step !== null) {
      const currentAngle = (state.current_step / state.steps_per_rev) * 360;
      cylinderGroup.setAttribute("transform", `rotate(${(-currentAngle).toFixed(2)})`);
    }

    for (const [posStr, el] of Object.entries(chamberEls)) {
      const pos = parseInt(posStr, 10);
      el.circle.classList.toggle("active", state.active_position === pos);
    }

    // statuspill
    statusPill.classList.remove("homed", "not-homed", "moving");
    if (state.moving && state.homing_progress !== null && state.homing_progress !== undefined) {
      statusPill.textContent = "Söker hem... (" + state.homing_progress + " steg)";
      statusPill.classList.add("moving");
    } else if (state.moving) {
      statusPill.textContent = "Rör sig...";
      statusPill.classList.add("moving");
    } else if (!state.homed) {
      statusPill.textContent = "Ej hemkörd";
      statusPill.classList.add("not-homed");
    } else if (state.active_position) {
      statusPill.textContent = "Position " + state.active_position;
      statusPill.classList.add("homed");
    } else {
      statusPill.textContent = "Hemkörd - mellanläge";
      statusPill.classList.add("homed");
    }

    simBadge.hidden = !state.simulated;

    if (state.current_step !== null) {
      stepReadout.textContent = `steg: ${state.current_step} / ${state.steps_per_rev}`;
    } else if (state.homing_progress !== null && state.homing_progress !== undefined) {
      stepReadout.textContent = `söker hem: ${state.homing_progress} steg`;
    } else {
      stepReadout.textContent = "steg: okänt";
    }

    if (state.error) {
      errorBanner.hidden = false;
      errorBanner.textContent = state.error;
    } else {
      errorBanner.hidden = true;
    }

    const disableMoves = state.moving || !state.homed;
    for (const btn of Object.values(posBtnEls)) btn.disabled = disableMoves;
    homeBtn.disabled = state.moving;

    const disableCal = state.moving || !state.homed;
    document.querySelectorAll(".btn-jog").forEach((b) => (b.disabled = disableCal));
    for (const [posStr, { btn, stepVal }] of Object.entries(saveBtnEls)) {
      btn.disabled = disableCal;
      const step = state.calibration[posStr];
      stepVal.textContent = step !== undefined ? `(${step})` : "";
    }
  }

  socket.on("connect", () => {
    socket.emit("get_state");
  });

  socket.on("state", render);

  homeBtn.addEventListener("click", () => socket.emit("home"));
  stopBtn.addEventListener("click", () => socket.emit("emergency_stop"));

  calibrateToggleBtn.addEventListener("click", () => {
    calibrationPanel.hidden = false;
  });
  closeCalibrationBtn.addEventListener("click", () => {
    calibrationPanel.hidden = true;
  });

  document.querySelectorAll(".btn-jog").forEach((btn) => {
    btn.addEventListener("click", () => {
      socket.emit("jog", { delta: parseInt(btn.dataset.jog, 10) });
    });
  });
})();
