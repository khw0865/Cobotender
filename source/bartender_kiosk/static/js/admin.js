let inventory = [];
let activeRequest = null;
function won(n){ return Number(n).toLocaleString('ko-KR') + '원'; }
for(let i=1;i<=6;i++){
  const row = document.createElement('div');
  row.className='joint-row';
  row.innerHTML = `<label>J${i}</label><input class="robot-control" type="range" min="-180" max="180" value="0" oninput="this.nextElementSibling.textContent=this.value+'°'"><span>0°</span>`;
  document.getElementById('jointControls').appendChild(row);
}
document.querySelectorAll('.tab').forEach(btn => btn.onclick = () => {
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active'); document.getElementById(btn.dataset.tab).classList.add('active');
  if(btn.dataset.tab === 'inventory') loadInventory();
  if(btn.dataset.tab === 'orders') loadOrders();
});
document.querySelectorAll('.robot-action').forEach(btn => btn.onclick = () => robotAction(btn.dataset.action));
async function robotAction(action){
  const res = await fetch('/api/robot/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
  const data = await res.json();
  if(!data.ok) alert(data.message);
  loadRobot();
}
async function finishMotion(){ await fetch('/api/robot/finish',{method:'POST'}); loadRobot(); }
async function loadRobot(){
  const res = await fetch('/api/robot'); const data = await res.json();
  const busy = data.state.busy === 1;
  document.getElementById('busyBanner').classList.toggle('hidden', !busy);
  document.querySelectorAll('.robot-control,.robot-action').forEach(el => el.disabled = busy);
  document.getElementById('logs').innerHTML = data.logs.map(l => `<div class="log-line">[${l.created_at}] ${l.message}</div>`).join('');
}
async function loadInventory(){
  const res = await fetch('/api/inventory'); inventory = await res.json();
  document.getElementById('inventoryList').innerHTML = inventory.map(x => {
    const bottles = Math.floor(x.stock_ml / x.bottle_ml);
    return `<div class="inv-card"><h3>${x.name}</h3><p>병 용량: ${x.bottle_ml}ml / 1잔: ${x.serving_ml}ml</p><p>현재 잔여량: ${x.stock_ml}ml</p><label>보유 병수</label><input data-id="${x.id}" type="number" min="0" value="${bottles}"></div>`;
  }).join('');
}
async function saveInventory(){
  const items = [...document.querySelectorAll('#inventoryList input')].map(i => ({id:i.dataset.id,bottles:i.value}));
  await fetch('/api/inventory',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});
  alert('재고가 저장되었습니다.'); loadInventory();
}
async function loadOrders(){
  const res = await fetch('/api/orders'); const data = await res.json();
  document.getElementById('orderRows').innerHTML = data.orders.map(o => {
    const items = o.items.map(i => `${i.menu_name} x ${i.qty}`).join('<br>');
    return `<tr><td>${o.created_at}</td><td>${o.order_number}</td><td>${items}</td><td>${won(o.total_price)}</td></tr>`;
  }).join('');
  document.getElementById('todaySales').textContent = won(data.total_today);
}
async function pollStaffRequests(){
  if(!document.getElementById('staffModal').classList.contains('hidden')) return;
  const res = await fetch('/api/staff_requests'); const rows = await res.json();
  if(rows.length > 0){
    activeRequest = rows[0];
    document.getElementById('staffMessage').textContent = `${activeRequest.created_at} - ${activeRequest.request_type} 요청이 들어왔습니다.`;
    document.getElementById('staffModal').classList.remove('hidden');
  }
}
async function handleStaffRequest(){
  if(activeRequest) await fetch(`/api/staff_requests/${activeRequest.id}/handle`,{method:'POST'});
  activeRequest = null;
  document.getElementById('staffModal').classList.add('hidden');
}
loadRobot(); loadInventory(); loadOrders();
setInterval(loadRobot, 2000);
setInterval(pollStaffRequests, 2000);
