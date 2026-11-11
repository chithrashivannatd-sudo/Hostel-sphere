const $ = id => document.getElementById(id);
document.querySelectorAll("[data-fill]").forEach(b => b.onclick = () => { $("u").value = b.dataset.u; $("p").value = b.dataset.p; $("p").focus(); });
document.querySelectorAll('input[type="password"]').forEach(inp => {
  let btn;
  const parent = inp.closest('.pw');
  if (parent) {
    btn = parent.querySelector('button');
  } else {
    const wrap = document.createElement('span');
    wrap.className = 'pw';
    inp.parentNode.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'link';
    btn.textContent = 'Show';
    wrap.appendChild(btn);
  }
  if (btn) {
    btn.onclick = (e) => {
      e.preventDefault();
      const isPw = inp.type === 'password';
      inp.type = isPw ? 'text' : 'password';
      btn.textContent = isPw ? 'Hide' : 'Show';
    };
  }
});
const mb = $("menu");
if (mb) mb.onclick = () => document.querySelector(".side").classList.toggle("open");
if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
  document.querySelectorAll(".stat b").forEach(el => {
    const m = el.textContent.match(/^(₹?)([\d,]+)$/); if (!m) return;
    const end = +m[2].replace(/,/g, ""), pre = m[1], step = Math.max(1, Math.ceil(end / 30)); let n = 0;
    const id = setInterval(() => { n = Math.min(end, n + step); el.textContent = pre + n.toLocaleString("en-IN"); if (n >= end) clearInterval(id); }, 25);
  });
}
document.querySelectorAll(".today-flag").forEach(e => e.closest("tr").classList.add("today"));
document.querySelectorAll("input[name=to]").forEach(c => c.onchange = () => { if (!c.checked) return; document.querySelectorAll("input[name=to]").forEach(x => { if (c.value === "All" ? x !== c : x.value === "All") x.checked = false; }); });
document.querySelectorAll(".chart-bar[data-height]").forEach(b => { b.style.height = b.dataset.height || b.getAttribute("data-height"); });
