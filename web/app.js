/* Only scenario names are sent; every product and order is synthetic. */
function element(tag, className, text) { const node=document.createElement(tag); if(className)node.className=className; if(text!==undefined)node.textContent=text; return node; }
let requestNumber=0;
async function render(key) {
  const request=++requestNumber;
  const products=document.getElementById('product-list');
  const list=document.getElementById('recommendation-list');
  document.getElementById('profile-description').textContent='Загрузка синтетического сценария…';
  document.getElementById('product-count').textContent='—';
  document.getElementById('recommendation-count').textContent='—';
  products.replaceChildren(); list.replaceChildren(element('p','empty','Расчёт…'));
  try {
    const response=await fetch('/api/recommend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario:key})});
    if(!response.ok) throw new Error('request failed');
    const data=await response.json();
    if(request!==requestNumber) return;
    document.getElementById('profile-description').textContent=data.description;
    document.getElementById('product-count').textContent=`${String(data.products.length).padStart(2,'0')} ПРОДУКТОВ`;
    document.getElementById('recommendation-count').textContent=`${String(data.recommendations.length).padStart(2,'0')} РЕШЕНИЙ`;
    if(!data.products.length) products.append(element('p','empty','В синтетической истории нет покупок.'));
    data.products.forEach((name,index)=>{const row=element('div','product-row');const icon=element('span','product-icon',['▦','▤','◇'][index%3]);icon.setAttribute('aria-hidden','true');const text=element('div','product-text');text.append(element('h3','',name),element('p','','Искусственный пример'));row.append(icon,text,element('span','product-status','ДЕМО'));products.append(row);});
    list.replaceChildren();
    const trace=element('div','paper');
    trace.setAttribute('id','decision-trace');
    const steps=element('ol','chain');
    const bases=data.recommendations.map(item=>`${item.name}: ${item.source} — ${item.fixture_evidence}`);
    const checks=data.trace.checks.map(item=>`${item.name}: ${item.accepted?'условно доступен':'отказ по имитации наличия'}`);
    const outcome=data.blocked_reason ? `Отказ: ${data.blocked_reason}` : `Показано ${data.recommendations.length} из 3.`;
    [['КОНТЕКСТ',`${data.description} ${data.trace.history_complete?'Полнота истории условно подтверждена.':'Полнота истории не подтверждена.'}`],
     ['КАНДИДАТЫ',checks.length ? `Дошли до имитации проверки: ${checks.map((_,i)=>data.trace.checks[i].name).join(', ')}. Повторные проверки возможны; ранние исключения здесь не перечислены.` : 'До проверки наличия кандидаты не дошли.'],
     ['ОСНОВАНИЕ',bases.length ? bases.join(' ') : 'Нет выданных рекомендаций; основания для них не заявляются.'],
     ['ПРОВЕРКА',checks.length ? `${checks.join('; ')}. Это имитация, не реальная проверка наличия.` : 'Имитация наличия не вызывалась.'],
     ['РЕЗУЛЬТАТ / ОТКАЗ',outcome]].forEach(([label,text])=>{const li=element('li');li.append(element('span','chain-label',label),element('span','',text));steps.append(li);});
    trace.append(element('h3','','Цепочка подбора'),steps); list.append(trace);
    if(data.blocked_reason) list.append(element('p','empty',`Рекомендации заблокированы: ${data.blocked_reason}`));
    else if(!data.recommendations.length) list.append(element('p','empty','Нет доступных рекомендаций.'));
    else if(data.recommendations.length<3) list.append(element('p','empty',`Показано ${data.recommendations.length} из 3: недостаточно кандидатов прошло ограничения подбора и синтетическую проверку. Пустые места не заполняются произвольно.`));
    data.recommendations.forEach((item,index)=>{const card=element('article','recommendation');const heading=element('div','rec-heading');heading.append(element('span','rec-number',`0${index+1}`),element('h3','',item.name));const details=element('details','reason');const summary=element('summary','','Почему этот товар показан');const chain=element('ol','chain');[['ИСТОЧНИК В HYBRID',item.source],['ОБЪЯСНЕНИЕ',item.explanation],['ФАКТ ИЗ ФИКСТУРЫ',item.fixture_evidence],['ОГРАНИЧЕНИЕ','Данные и проверка наличия полностью синтетические; реальная доступность не проверялась.']].forEach(([label,text])=>{const li=element('li');li.append(element('span','chain-label',label),element('span','',text));chain.append(li);});details.append(summary,chain);card.append(element('span','rec-maker','СИНТЕТИЧЕСКИЙ ПРОДУКТ'),heading,details);list.append(card);});
  } catch(error) {
    if(request!==requestNumber) return;
    document.getElementById('profile-description').textContent='Сервис недоступен.';
    list.replaceChildren(element('p','empty','Не удалось получить результат. Запустите локальный demo_server.py; готовые подборки не подставляются.'));
  }
}
document.querySelectorAll('input[name="profile"]').forEach(input=>input.addEventListener('change',event=>render(event.target.value)));
render(document.querySelector('input[name="profile"]:checked').value);
