let menu = [];
let currentCategory = 'cocktail';
let selectedMenu = null;
let cart = [];
const slides = ['/static/images/slide_gatsby.jpg','/static/images/slide_gil_beer.jpg','/static/images/slide_public_warning.jpg'];
const loadingTexts = ['얼음을 깎는중...','맛있게 마는 중...','흘린 액체 닦는 중...'];
let slideIndex = 0;

setInterval(() => {
  const img = document.getElementById('slideImage');
  if (!img) return;
  slideIndex = (slideIndex + 1) % slides.length;
  img.src = slides[slideIndex];
}, 3000);

async function loadMenu(){
  const res = await fetch('/api/menu');
  menu = await res.json();
  renderMenu();
}
function won(n){ return Number(n).toLocaleString('ko-KR') + '원'; }
function showOrderScreen(){
  document.getElementById('standby').classList.add('hidden');
  document.getElementById('orderScreen').classList.remove('hidden');
  loadMenu();
}
document.querySelectorAll('.cat').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.cat').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCategory = btn.dataset.category;
    renderMenu();
  });
});
function renderMenu(){
  const grid = document.getElementById('menuGrid');
  if (!grid) return;
  grid.innerHTML = '';
  menu.filter(m => m.category === currentCategory).forEach(m => {
    const disabled = m.sold_out === 1;
    const card = document.createElement('div');
    card.className = 'menu-card' + (disabled ? ' disabled' : '');
    card.innerHTML = `
      ${disabled ? '<div class="soldout">품절</div>' : ''}
      <img src="/static/images/${m.image}" alt="${m.name}" onerror="this.src='/static/images/placeholder.jpg'">
      <div class="info"><h3>${m.name}</h3><p>${won(m.price)}</p></div>`;
    card.onclick = () => openPopup(m);
    grid.appendChild(card);
  });
}
function openPopup(m){
  selectedMenu = m;
  document.getElementById('popupImage').src = '/static/images/' + m.image;
  document.getElementById('popupImage').onerror = e => e.target.src='/static/images/placeholder.jpg';
  document.getElementById('popupName').textContent = m.name;
  document.getElementById('popupDesc').textContent = m.description;
  document.getElementById('popupPrice').textContent = won(m.price);
  document.getElementById('qtyInput').value = 1;
  document.getElementById('popup').classList.remove('hidden');
}
function closePopup(){ document.getElementById('popup').classList.add('hidden'); }
function changeQty(delta){
  const input = document.getElementById('qtyInput');
  input.value = Math.max(1, Number(input.value || 1) + delta);
}
function addToCart(){
  const qty = Math.max(1, Number(document.getElementById('qtyInput').value || 1));
  const found = cart.find(x => x.id === selectedMenu.id);
  if (found) found.qty += qty;
  else cart.push({id:selectedMenu.id, name:selectedMenu.name, price:selectedMenu.price, qty});
  closePopup();
  renderCart();
}
function renderCart(){
  const box = document.getElementById('cartItems');
  if (cart.length === 0){ box.className='cart-items empty'; box.textContent='담긴 메뉴가 없습니다.'; }
  else {
    box.className='cart-items';
    box.innerHTML = cart.map((x,i) => `
      <div class="cart-line">
        <span class="cart-menu-name">${x.name}</span>
        <div class="cart-qty-control">
          <button onclick="changeCartQty(${i},-1)">-</button>
          <span>${x.qty}</span>
          <button onclick="changeCartQty(${i},1)">+</button>
        </div>
        <span class="cart-price">${won(x.price*x.qty)}</span>
        <button class="cart-delete" onclick="removeCart(${i})">삭제</button>
      </div>`).join('');
  }
  document.getElementById('cartTotal').textContent = won(cart.reduce((s,x)=>s+x.price*x.qty,0));
}
function changeCartQty(i, delta){
  cart[i].qty += delta;
  if (cart[i].qty <= 0) cart.splice(i,1);
  renderCart();
}
function removeCart(i){ cart.splice(i,1); renderCart(); }
async function submitOrder(){
  if (cart.length === 0){ alert('장바구니가 비어있습니다.'); return; }
  const res = await fetch('/api/order', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({items:cart})});
  const data = await res.json();
  if (!data.ok){ alert(data.message || '주문 실패'); loadMenu(); return; }
  document.getElementById('orderScreen').classList.add('hidden');
  document.getElementById('loadingScreen').classList.remove('hidden');
  let idx = 0;
  const textTimer = setInterval(() => {
    idx = (idx + 1) % loadingTexts.length;
    document.getElementById('loadingText').textContent = loadingTexts[idx];
  }, 3000);
  setTimeout(() => {
    clearInterval(textTimer);
    document.getElementById('loadingScreen').classList.add('hidden');
    document.getElementById('completeScreen').classList.remove('hidden');
    cart = []; renderCart(); loadMenu();
    setTimeout(() => {
      document.getElementById('completeScreen').classList.add('hidden');
      document.getElementById('standby').classList.remove('hidden');
    }, 5000);
  }, 9000);
}
