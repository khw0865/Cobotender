function setText(id, text){
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function addLog(message){
  const box = document.getElementById('logBox');
  if (!box) return;
  const now = new Date().toLocaleTimeString('ko-KR', {hour12:false});
  box.insertAdjacentHTML('afterbegin', `<p>[${now}] ${message}</p>`);
}

function renderLogs(logs){
  const box = document.getElementById('logBox');
  if (!box || !Array.isArray(logs)) return;
  box.innerHTML = logs.map(log => `<p>${log}</p>`).join('') || '<p>[SYSTEM] 로그 대기 중</p>';
}

function updateMode(mode){
  const badge = document.getElementById('modeBadge');
  const cleanMode = String(mode || 'IDLE').toLowerCase();
  if (badge) badge.className = 'mode-badge ' + cleanMode;
  setText('modeText', mode || 'IDLE');
  setText('currentMode', mode || 'IDLE');
}

function updateJoints(joints){
  for (let index = 0; index < 6; index++){
    const jointNo = index + 1;
    const angle = Number((joints || [])[index] || 0);
    const percent = Math.max(0, Math.min(100, (angle + 180) / 360 * 100));
    setText('joint' + jointNo, angle.toFixed(1) + '°');
    const bar = document.getElementById('jointBar' + jointNo);
    if (bar) bar.style.width = percent + '%';
  }
}

function updateTaskSteps(taskIndex){
  document.querySelectorAll('#taskSteps li').forEach((item, index) => {
    item.classList.remove('done', 'active');
    if (index < taskIndex) item.classList.add('done');
    if (index === taskIndex) item.classList.add('active');
  });
}

function updateDashboard(data){
  updateMode(data.mode);
  updateJoints(data.joints || []);
  setText('currentRecipe', data.recipe || '대기 중');
  setText('currentStep', data.step || 'Ready');
  setText('linearSpeed', data.speed?.linear || 0);
  setText('angularSpeed', data.speed?.angular || 0);
  updateTaskSteps(data.taskIndex || 0);
  renderLogs(data.logs);
}

async function fetchRobotStatus(){
  try{
    const res = await fetch('/api/robot/status');
    if (!res.ok) throw new Error('status api error');
    const data = await res.json();
    updateDashboard(data);
  }catch(e){
    updateDashboard({
      mode:'ERROR',
      joints:[0,0,0,0,0,0],
      recipe:'상태 수신 실패',
      step:'API 연결 확인 필요',
      speed:{linear:0,angular:0},
      taskIndex:0,
      logs:['[SYSTEM] /api/robot/status 연결 실패']
    });
  }
}

async function sendRobotCommand(command){
  try{
    const res = await fetch('/api/robot/command', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({command})
    });
    const data = await res.json();
    addLog(data.message || `${command} command sent.`);
    fetchRobotStatus();
  }catch(e){
    addLog(`${command} 명령 전송 실패`);
  }
}

fetchRobotStatus();
setInterval(fetchRobotStatus, 1000);



let staffRequestPopupOpen = false;
let pendingStaffRequests = [];

function staffRequestIcon(type){
  if (type === '물') return '💧';
  if (type === '냅킨') return '🧻';
  if (type === '직원호출') return '🔔';
  return '📌';
}

function playStaffRequestSound(){
  try{
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.22);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.24);
  }catch(e){
    /* 브라우저 정책상 소리가 차단될 수 있으므로 무시 */
  }
}

function showStaffRequestModal(requests){
  const modal = document.getElementById('staffRequestModal');
  const list = document.getElementById('staffRequestList');
  if (!modal || !list) return;

  pendingStaffRequests = requests;
  staffRequestPopupOpen = true;

  list.innerHTML = requests.map(req => `
    <div class="staff-request-item">
      <b>${staffRequestIcon(req.request_type)}</b>
      <span>${req.request_type}</span>
    </div>
  `).join('');

  modal.classList.remove('hidden');
  playStaffRequestSound();
}

async function confirmStaffRequests(){
  const modal = document.getElementById('staffRequestModal');
  if (modal) modal.classList.add('hidden');

  const requests = pendingStaffRequests.slice();
  pendingStaffRequests = [];

  for (const req of requests){
    try{
      await fetch(`/api/staff_requests/${req.id}/handle`, {
        method:'POST'
      });
    }catch(e){
      addLog(`${req.request_type} 요청 처리 실패`);
    }
  }

  staffRequestPopupOpen = false;
}

async function checkStaffRequests(){
  try{
    if (staffRequestPopupOpen) return;

    const res = await fetch('/api/staff_requests');
    if (!res.ok) throw new Error('staff requests api error');

    const requests = await res.json();

    if (!Array.isArray(requests) || requests.length === 0) return;

    showStaffRequestModal(requests);

  }catch(e){
    staffRequestPopupOpen = false;
  }
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && staffRequestPopupOpen){
    confirmStaffRequests();
  }
});

checkStaffRequests();
setInterval(checkStaffRequests, 1000);
