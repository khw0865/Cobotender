const demoState = {
  mode: 'IDLE',
  joints: [0, 0, 90, 0, 90, 0],
  recipe: '대기 중',
  step: 'Ready',
  connections: {ros: true, mcu: true, plc: true},
  speed: {linear: 0, angular: 0},
  resource: {cpu: 18, memory: 42, temperature: 47},
  taskIndex: 0
};

function setText(id, text){
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function addLog(message){
  const box = document.getElementById('logBox');
  const now = new Date().toLocaleTimeString('ko-KR', {hour12:false});
  box.insertAdjacentHTML('afterbegin', `<p>[${now}] ${message}</p>`);
}

function updateMode(mode){
  const badge = document.getElementById('modeBadge');
  const cleanMode = String(mode || 'IDLE').toLowerCase();
  badge.className = 'mode-badge ' + cleanMode;
  setText('modeText', mode || 'IDLE');
  setText('currentMode', mode || 'IDLE');
}

function updateJoints(joints){
  joints.forEach((value, index) => {
    const jointNo = index + 1;
    const angle = Number(value || 0);
    const percent = Math.max(0, Math.min(100, (angle + 180) / 360 * 100));
    setText('joint' + jointNo, angle.toFixed(1) + '°');
    document.getElementById('jointBar' + jointNo).style.width = percent + '%';
  });
}

function updateConnection(name, connected){
  const dot = document.getElementById(name + 'State');
  if (dot) dot.className = 'conn-dot' + (connected ? ' on' : '');
  setText(name + 'Text', connected ? 'CONNECTED' : 'DISCONNECTED');
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
  updateConnection('ros', data.connections?.ros);
  updateConnection('mcu', data.connections?.mcu);
  updateConnection('plc', data.connections?.plc);
  setText('linearSpeed', data.speed?.linear || 0);
  setText('angularSpeed', data.speed?.angular || 0);
  setText('cpuUsage', (data.resource?.cpu || 0) + '%');
  setText('memoryUsage', (data.resource?.memory || 0) + '%');
  setText('temperature', (data.resource?.temperature || 0) + '°C');
  updateTaskSteps(data.taskIndex || 0);
}

async function fetchRobotStatus(){
  try{
    const res = await fetch('/api/robot/status');
    if (!res.ok) throw new Error('status api not ready');
    const data = await res.json();
    updateDashboard(data);
  }catch(e){
    updateDashboard(demoState);
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
    addLog(`${command} command requested. API 연결 필요.`);
  }
}

fetchRobotStatus();
setInterval(fetchRobotStatus, 1000);
