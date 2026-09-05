"use strict";

// dummy_web 이 보내는 상태 스냅샷을 그대로 그린다. 화면은 자체 상태를 보관하지 않는다.
// 그래야 터미널에서 `ros2 service call` 로 바꾼 값도 어긋남 없이 그대로 반영된다.

const $ = (id) => document.getElementById(id);

// 토픽 수신이 이 시간을 넘겨 끊기면 값을 믿을 수 없다고 본다.
const STALE_SEC = 2.0;

let lastState = null;

// ------------------------------------------------------------------ //
// 서버 호출
// ------------------------------------------------------------------ //
async function post(path, body) {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      pushLocalLog("error", data.error || `요청 실패 (${res.status})`);
      return null;
    }
    return data.result;
  } catch (err) {
    pushLocalLog("error", `요청을 보내지 못했습니다: ${err.message}`);
    return null;
  }
}

const callService = (key, body) => post(`/api/service/${key}`, body);

// ------------------------------------------------------------------ //
// SSE 수신
// ------------------------------------------------------------------ //
function connect() {
  const source = new EventSource("/api/events");

  source.onopen = () => setConn("ok", "연결됨");
  source.onmessage = (event) => {
    lastState = JSON.parse(event.data);
    render(lastState);
  };
  source.onerror = () => {
    setConn("down", "웹 서버 연결 끊김 — 재연결 시도 중");
    // EventSource 는 스스로 재연결한다. 별도 처리는 하지 않는다.
  };
}

function setConn(cls, text) {
  const el = $("conn");
  el.className = `conn ${cls}`;
  el.textContent = text;
}

// ------------------------------------------------------------------ //
// 그리기
// ------------------------------------------------------------------ //
function render(state) {
  renderPerson(state.person);
  renderLoadCell(state.load_cell, state.tray_count);
  renderTrayDoors(state.tray_door, state.close_job, state.tray_count);
  renderFloor(state.floor);
  renderNotify(state.notify_fail);
  renderSideDoor(state.side_door);
  renderPhidgetLoadCell(state.phidget_load_cell, state.tracking, state.tray_count);
  renderSideDoors(state.side_doors, state.door_jobs, state.door_names);
  renderLog(state.log);
  renderConnFromTopics(state);
}

function renderConnFromTopics(state) {
  // 웹 서버는 붙어 있어도 dummy_node 가 죽으면 토픽이 끊긴다. 그 둘을 구분해 알린다.
  const sections = [state.person, state.load_cell, state.tray_door, state.phidget_load_cell];
  const seen = sections.filter((s) => s.seen);
  if (seen.length === 0) {
    setConn("down", "dummy_node 로부터 아직 수신 없음");
  } else if (seen.some((s) => s.age_sec > STALE_SEC)) {
    setConn("down", "dummy_node 발행 끊김");
  } else {
    setConn("ok", "연결됨");
  }
}

function ageText(section) {
  if (!section.seen) return "수신 없음";
  const stamp =
    section.stamp_age_ms !== undefined
      ? `stamp ${section.stamp_age_ms.toFixed(0)}ms · `
      : "";
  return `${stamp}수신 ${section.age_sec.toFixed(1)}s 전`;
}

function applyAge(el, section) {
  el.textContent = ageText(section);
  el.className = `meta${!section.seen || section.age_sec > STALE_SEC ? " stale" : ""}`;
}

function renderPerson(section) {
  const badge = $("person-value");
  if (!section.seen) {
    badge.textContent = "—";
    badge.className = "badge";
  } else {
    badge.textContent = section.is_person ? "사람 있음" : "사람 없음";
    badge.className = `badge ${section.is_person ? "on" : "off"}`;
  }
  applyAge($("person-age"), section);
}

function renderLoadCell(section, trayCount) {
  const host = $("load-cell-trays");
  const trays = section.seen ? section.trays : placeholderTrays(trayCount);

  syncRows(host, trays.length, (tray) => `
    <span class="tray-name">tray ${tray}</span>
    <span class="badge" data-role="occupied">—</span>
    <span class="badge" data-role="healthy">—</span>
    <span class="meta" data-role="weight"></span>
    <button data-service="load_cell_occupied" data-trays="[${tray}]" data-toggle="occupied">감지 전환</button>
    <button data-service="load_cell_healthy" data-trays="[${tray}]" data-toggle="healthy">고장 전환</button>
  `);

  trays.forEach((entry, i) => {
    const row = host.children[i];
    const occupied = row.querySelector('[data-role="occupied"]');
    const healthy = row.querySelector('[data-role="healthy"]');
    if (!section.seen) {
      occupied.textContent = "—";
      occupied.className = "badge";
      healthy.textContent = "—";
      healthy.className = "badge";
      row.querySelector('[data-role="weight"]').textContent = "";
      return;
    }
    occupied.textContent = entry.occupied ? "물건 있음" : "비어 있음";
    occupied.className = `badge ${entry.occupied ? "on" : "off"}`;
    healthy.textContent = entry.healthy ? "정상" : "확인 불가";
    healthy.className = `badge ${entry.healthy ? "off" : "alert"}`;
    row.querySelector('[data-role="weight"]').textContent = `${entry.weight_g} g`;
  });
}

function renderTrayDoors(section, job, trayCount) {
  const host = $("tray-doors");
  const doors = section.seen ? section.doors : placeholderTrays(trayCount);

  syncRows(host, doors.length, (tray) => `
    <span class="tray-name">tray ${tray}</span>
    <span class="badge" data-role="phase">—</span>
    <span class="badge" data-role="obstructed">—</span>
    <button data-service="tray_door_open" data-trays="[${tray}]">열기</button>
    <button data-close="[${tray}]">닫기</button>
    <button data-service="tray_door_obstructed" data-trays="[${tray}]" data-toggle="obstructed">끼임 전환</button>
  `);

  doors.forEach((door, i) => {
    const row = host.children[i];
    const phase = row.querySelector('[data-role="phase"]');
    const obstructed = row.querySelector('[data-role="obstructed"]');
    if (!section.seen) {
      phase.textContent = "—";
      phase.className = "badge";
      obstructed.textContent = "—";
      obstructed.className = "badge";
      return;
    }
    phase.textContent = PHASE_LABEL[door.phase] || door.phase;
    phase.className = `badge ${PHASE_CLASS[door.phase] || ""}`;
    obstructed.textContent = door.obstructed ? "끼임" : "끼임 없음";
    obstructed.className = `badge ${door.obstructed ? "alert" : "off"}`;
  });

  $("cancel-close").disabled = !job.active;
  $("close-job").innerHTML = closeJobText(job);
}

const PHASE_LABEL = {
  open: "열림",
  closed: "닫힘",
  opening: "열리는 중",
  closing: "닫히는 중",
  moving: "이동 중",
};

const PHASE_CLASS = {
  open: "on",
  closed: "off",
  opening: "moving",
  closing: "moving",
  moving: "moving",
};

function closeJobText(job) {
  if (job.active) {
    return `닫기 진행 중 — 대상 [${job.trays}], force=${job.force}, ` +
      `남은 tray [${job.remaining}]${job.obstructed ? ", <b>끼임 감지</b>" : ""}`;
  }
  if (job.last_result) {
    const r = job.last_result;
    return `마지막 닫기 결과 — <b>${r.error_name}</b> · ` +
      `닫힘 [${r.closed}] / 실패 [${r.failed}] · ${escapeHtml(r.message)}`;
  }
  return "진행 중인 닫기 goal 이 없다.";
}

function renderFloor(section) {
  const badge = $("floor-value");
  badge.textContent = section.seen ? `${section.current}층` : "—";
  badge.className = `badge big ${section.seen ? "on" : ""}`;
  applyAge($("floor-age"), section);
}

function renderNotify(value) {
  const badge = $("notify-value");
  if (value === null || value === undefined) {
    badge.textContent = "알 수 없음";
    badge.className = "badge";
    return;
  }
  badge.textContent = value ? "거부 중" : "정상 수신";
  badge.className = `badge ${value ? "alert" : "off"}`;
}

function renderSideDoor(section) {
  const badge = $("side-door-value");
  if (!section.seen) {
    badge.textContent = "—";
    badge.className = "badge";
    return;
  }
  badge.textContent = section.open ? "열림" : "닫힘";
  badge.className = `badge ${section.open ? "on" : "off"}`;
}

/** 로케일에 따라 "19시 26분 19초" 처럼 길어지지 않도록 직접 포맷한다. */
function clockText(epochSec) {
  const d = new Date(epochSec * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function renderLog(entries) {
  const host = $("log");
  const atBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 24;
  host.innerHTML = entries
    .slice()
    .reverse()
    .map((e) => {
      const t = clockText(e.at);
      return `<div class="${e.level}"><time>${t}</time>${escapeHtml(e.message)}</div>`;
    })
    .join("");
  if (atBottom) host.scrollTop = 0;
}

// ------------------------------------------------------------------ //
// 실물 모사 인터페이스 (phidget_load_cell · side_door)
// ------------------------------------------------------------------ //

function renderPhidgetLoadCell(section, tracking, trayCount) {
  const host = $("phidget-trays");
  const trays = section.seen ? section.trays : placeholderTrays(trayCount);

  // 물건 놓기/빼기는 토글이 아니라 명시 버튼이다. tracking 이 꺼져 있으면 발행값(동결)과
  // 판 위의 실제 상황이 다르므로, 발행값을 뒤집는 토글은 엉뚱한 값을 보낸다.
  syncRows(host, trays.length, (tray) => `
    <span class="tray-name">tray ${tray}</span>
    <span class="badge" data-role="occupied">—</span>
    <span class="badge" data-role="healthy">—</span>
    <span class="badge" data-role="tracking">—</span>
    <span class="meta" data-role="weight"></span>
    <button data-service="phidget_occupied" data-trays="[${tray}]" data-value="true">물건 놓기</button>
    <button data-service="phidget_occupied" data-trays="[${tray}]" data-value="false">물건 빼기</button>
    <button data-service="phidget_healthy" data-trays="[${tray}]" data-toggle="p_healthy">고장 전환</button>
    <button data-service="phidget_set_tracking" data-trays="[${tray}]" data-value="true">측정 재개</button>
    <button data-service="phidget_confirm_load" data-trays="[${tray}]">적재 확정</button>
    <button data-service="phidget_confirm_unload" data-trays="[${tray}]">반출 확정</button>
    <button data-service="phidget_tare" data-trays="[${tray}]">tare</button>
  `);

  trays.forEach((entry, i) => {
    const row = host.children[i];
    const occupied = row.querySelector('[data-role="occupied"]');
    const healthy = row.querySelector('[data-role="healthy"]');
    const track = row.querySelector('[data-role="tracking"]');

    const value = tracking ? tracking[i] : null;
    const known = value !== null && value !== undefined;
    track.textContent = known ? (value ? "측정 중" : "동결") : "tracking ?";
    track.className = `badge ${known ? (value ? "on" : "moving") : ""}`;
    track.title = "tracking 은 토픽에 없어 웹에서 보낸 명령 기준으로 표시한다";

    if (!section.seen) {
      occupied.textContent = "—";
      occupied.className = "badge";
      healthy.textContent = "—";
      healthy.className = "badge";
      row.querySelector('[data-role="weight"]').textContent = "";
      return;
    }
    occupied.textContent = entry.occupied ? "물건 있음" : "비어 있음";
    occupied.className = `badge ${entry.occupied ? "on" : "off"}`;
    healthy.textContent = entry.healthy ? "정상" : "확인 불가";
    healthy.className = `badge ${entry.healthy ? "off" : "alert"}`;
    row.querySelector('[data-role="weight"]').textContent = `${entry.weight_g} g`;
  });
}

const DOOR_LABEL = ["top (tray 0)", "bottom (tray 1)"];

function renderSideDoors(doors, jobs, names) {
  const host = $("side-doors");

  // 끼임·손으로 움직임은 명시 버튼이다 — 끼임 주입 상태는 토픽에 없어 토글할 기준이 없다.
  syncRows(host, names.length, (idx) => `
    <span class="tray-name" data-role="name">문 ${idx}</span>
    <span class="badge" data-role="status">—</span>
    <span class="meta" data-role="age"></span>
    <button data-door="${idx}" data-command="unlock">unlock (열기)</button>
    <button data-door="${idx}" data-command="open">open (활짝)</button>
    <button data-door="${idx}" data-command="close">close</button>
    <button data-door-cancel="${idx}" class="danger" disabled>취소</button>
    <button data-service="side_door_obstructed" data-trays="[${idx}]" data-value="true">끼임 on</button>
    <button data-service="side_door_obstructed" data-trays="[${idx}]" data-value="false">끼임 off</button>
    <button data-service="side_door_manual" data-trays="[${idx}]" data-value="true">손으로 닫기</button>
    <button data-service="side_door_manual" data-trays="[${idx}]" data-value="false">손으로 열기</button>
    <div class="job" data-role="job"></div>
  `);

  names.forEach((name, i) => {
    const row = host.children[i];
    const section = doors[i] || { seen: false };
    const job = jobs[i] || { active: false, last_result: null };

    row.querySelector('[data-role="name"]').textContent =
      `${DOOR_LABEL[i] || `문 ${i}`} · ${name}`;

    const status = row.querySelector('[data-role="status"]');
    if (!section.seen) {
      status.textContent = "—";
      status.className = "badge";
    } else {
      const closed = section.status === "closed";
      status.textContent = closed ? "닫힘" : "열림";
      status.className = `badge ${closed ? "off" : "on"}`;
    }
    applyAge(row.querySelector('[data-role="age"]'), section);

    row.querySelector("[data-door-cancel]").disabled = !job.active;
    row.querySelector('[data-role="job"]').innerHTML = doorJobText(job);
  });
}

function doorJobText(job) {
  if (job.active) {
    const retries = job.retries ? `, 끼임 재시도 ${job.retries}` : "";
    return `<b>${job.command}</b> 진행 중 — 상태 ${escapeHtml(job.state ?? "…")}, ` +
      `${Number(job.elapsed || 0).toFixed(1)}s${retries}`;
  }
  if (job.last_result) {
    const r = job.last_result;
    return `마지막 결과 — <b>${r.status}</b> · ${r.error_name} (${r.error_code}) · ` +
      escapeHtml(r.message);
  }
  return "진행 중인 goal 이 없다.";
}

// ------------------------------------------------------------------ //
// DOM 헬퍼
// ------------------------------------------------------------------ //
function placeholderTrays(count) {
  return Array.from({ length: Math.max(count, 1) }, (_, i) => ({ tray: i }));
}

/** 행 개수가 바뀔 때만 다시 만든다. 매번 다시 그리면 버튼 클릭이 씹힌다. */
function syncRows(host, count, template) {
  if (host.childElementCount === count) return;
  host.innerHTML = "";
  for (let tray = 0; tray < count; tray += 1) {
    const row = document.createElement("div");
    row.className = "tray";
    row.innerHTML = template(tray);
    host.appendChild(row);
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function pushLocalLog(level, message) {
  // 서버에 닿지 못한 오류는 서버 로그에 남지 않으므로 화면에서만 보여 준다.
  const host = $("log");
  const t = clockText(Date.now() / 1000);
  host.insertAdjacentHTML(
    "afterbegin",
    `<div class="${level}"><time>${t}</time>${escapeHtml(message)}</div>`
  );
}

// ------------------------------------------------------------------ //
// 조작
// ------------------------------------------------------------------ //

/** 현재 스냅샷에서 tray 하나의 값을 읽어 반대 값을 만든다. */
function currentValue(toggle, tray) {
  if (!lastState) return false;
  if (toggle === "occupied" || toggle === "healthy") {
    const section = lastState.load_cell;
    if (!section.seen) return false;
    const entry = section.trays.find((t) => t.tray === tray);
    return entry ? entry[toggle] : false;
  }
  if (toggle === "p_healthy") {
    const section = lastState.phidget_load_cell;
    if (!section.seen) return false;
    const entry = section.trays.find((t) => t.tray === tray);
    return entry ? entry.healthy : false;
  }
  if (toggle === "obstructed") {
    const section = lastState.tray_door;
    if (!section.seen) return false;
    const door = section.doors.find((d) => d.tray === tray);
    return door ? door.obstructed : false;
  }
  return false;
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;

  // 실물 문 명령(액션). 문마다 서버가 따로라 인덱스로 고른다.
  if (button.dataset.door !== undefined) {
    post(`/api/action/door/${button.dataset.door}`, { command: button.dataset.command });
    return;
  }
  if (button.dataset.doorCancel !== undefined) {
    post(`/api/action/door/${button.dataset.doorCancel}/cancel`, {});
    return;
  }

  // 문 닫기(액션)
  if (button.dataset.close !== undefined) {
    post("/api/action/close", {
      trays: JSON.parse(button.dataset.close),
      force: $("force").checked,
    });
    return;
  }

  // 서비스 호출
  const key = button.dataset.service;
  if (!key) return;

  const body = {};
  if (button.dataset.trays !== undefined) {
    body.trays = JSON.parse(button.dataset.trays);
  }
  if (button.dataset.toggle) {
    const tray = body.trays[0];
    body.value = !currentValue(button.dataset.toggle, tray);
  } else if (button.dataset.value !== undefined) {
    body.value = button.dataset.value === "true";
  }
  callService(key, body);
});

$("close-all").addEventListener("click", () => {
  post("/api/action/close", { trays: [], force: $("force").checked });
});

$("cancel-close").addEventListener("click", () => {
  post("/api/action/close/cancel", {});
});

function floorValue() {
  const value = parseInt($("floor-input").value, 10);
  if (Number.isNaN(value)) {
    pushLocalLog("error", "층 값을 숫자로 입력하세요.");
    return null;
  }
  if (value === 0) {
    // 서버도 막지만, 여기서 먼저 걸러야 왕복 없이 이유를 알 수 있다.
    pushLocalLog("error", "0층은 존재하지 않습니다. 지하 1층은 -1 입니다.");
    return null;
  }
  return value;
}

$("floor-current").addEventListener("click", () => {
  const value = floorValue();
  if (value !== null) callService("floor_current", { value });
});

$("floor-target").addEventListener("click", () => {
  const value = floorValue();
  if (value !== null) callService("floor_target", { value });
});

connect();
