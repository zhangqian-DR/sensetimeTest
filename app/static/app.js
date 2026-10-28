/* 页面只调用已有业务接口，不在浏览器里推导问题结论或改写提醒计数。 */
'use strict';
const $ = id => document.getElementById(id);
const state = {token:'',epoch:0,read:0,loading:false,busy:false,health:null,user:null,
  issues:null,refunds:[],tasks:[],messages:[],selected:null,lastEvent:null,logs:[]};
const names = {OPEN:'待确认',CONFIRMED_PROBLEM:'已确认有问题',NORMAL:'正常购买',RESOLVED:'已解决',CLOSED:'已关闭',
  PENDING:'待发送',SUBMITTING:'提交中',SENT:'已发送',FAILED:'发送失败',UNKNOWN:'结果未知',CANCELLED:'已取消',
  INITIAL:'首次提醒',AUTO_FOLLOWUP:'自动追加',USER_SCHEDULED:'用户约定',WAITING_FACT:'等待事实或新约定',
  AWAITING_REPLY:'等待用户回复',STOPPED:'已停止跟进',DUPLICATE:'疑似重复消费',REFUND_OVERDUE:'退款逾期'};
const terminal = status => ['NORMAL','RESOLVED','CLOSED'].includes(status);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = value => typeof value==='number' && Number.isFinite(value) ? '¥'+(value/100).toFixed(2) : '待补充';
const time = value => value==null ? '—' : new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const iso = value => new Date(value).toISOString();
const eventId = () => crypto.randomUUID();
const badge = status => `<span class="badge ${esc(status)} status-${esc(status).toLowerCase()}">${esc(names[status]||status)}</span>`;
const empty = text => `<div class="empty-state">${esc(text)}</div>`;
let toastTimer;

function notify(message,error=false) {
  $('toast').textContent=message;
  $('toast').classList.toggle('is-error',error);
  $('toast').hidden=false;
  clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>$('toast').hidden=true,5500);
}
function log(message) {
  state.logs.unshift({at:Date.now(),message});
  state.logs=state.logs.slice(0,12);
  $('operation-log').innerHTML=state.logs.map(x=>`<div class="log-item"><small>${esc(new Date(x.at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}))}</small><span>${esc(x.message)}</span></div>`).join('');
}
async function request(path,options={},admin=false,token=state.token) {
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),12000);
  try {
    const response=await fetch(path,{...options,signal:controller.signal,
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+(admin?$('admin-token').value.trim():token)}});
    const data=await response.json().catch(()=>null);
    if(!response.ok) {
      const error=new Error(`HTTP ${response.status}：${typeof data?.detail==='string'?data.detail:'请求未完成，请检查授权和输入。'}`);
      error.status=response.status;
      throw error;
    }
    return data;
  } catch(error) {
    if(error.name==='AbortError') throw new Error('请求超时，执行结果可能未知，请先刷新核对，勿重复创建事件。');
    throw error;
  } finally { clearTimeout(timer); }
}
const write = (path,payload,method='POST',admin=false) => request(path,{method,body:JSON.stringify(payload)},admin);

function updateControls() {
  for(const button of document.querySelectorAll('button')) button.disabled=state.busy;
  $('user-select').disabled=state.busy;
  for(const button of document.querySelectorAll('[data-hours]')) button.disabled=state.busy||state.health?.time_mode!=='simulated';
  for(const button of document.querySelectorAll('[data-demo="arrival60"],[data-demo="arrival40"]')) button.disabled=state.busy||!$('refund-no').value.trim();
  document.querySelector('[data-demo="replay"]').disabled=state.busy||!state.lastEvent;
}
async function refresh(force=false) {
  if(state.loading&&!force) return;
  state.loading=true;
  const epoch=state.epoch, read=++state.read, token=state.token;
  try {
    const [health,user,issues,refunds,tasks,messages]=await Promise.all([
      request('/health',{},false,token),request('/me',{},false,token),request('/issues',{},false,token),
      request('/refunds',{},false,token),request('/reminders',{},false,token),request('/notifications',{},false,token)]);
    // 旧身份或旧批次的迟到响应不可覆盖当前用户的数据。
    if(epoch!==state.epoch||read!==state.read) return;
    Object.assign(state,{health,user,issues,refunds,tasks,messages});
    $('error-banner').hidden=true;
    $('last-updated').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
    render();
  } catch(error) {
    if(epoch!==state.epoch||read!==state.read) return;
    $('error-banner').hidden=false;
    $('error-banner').textContent='刷新失败：'+error.message+(state.issues?'。当前保留上次成功加载的数据。':'。尚未取得账目数据，不代表没有问题。');
    $('system-status').textContent='连接未确认';
  } finally {state.loading=false;updateControls();}
}
async function connect() {
  state.epoch++;
  state.token=$('user-token').value.trim();
  Object.assign(state,{issues:null,refunds:[],tasks:[],messages:[],selected:null,lastEvent:null,user:null,health:null,logs:[]});
  $('refund-no').value='';
  $('operation-log').innerHTML=empty('本次连接尚无操作。');
  $('last-updated').textContent='正在连接…';
  render();
  await refresh(true);
}
function render() {
  const loaded=state.issues!==null;
  $('system-status').textContent=state.health?`${state.health.time_mode==='simulated'?'模拟时钟':'真实时钟'} · ${state.user?.user_id||''} · ${state.health.background_running?'后台检查中':'后台已停止'}`:'等待连接';
  $('clock-value').textContent=state.health?time(state.health.now_ms):'—';
  $('stat-open').textContent=loaded?state.issues.filter(i=>!terminal(i.status)).length:'—';
  $('stat-refunds').textContent=loaded?state.refunds.length:'—';
  $('stat-reminders').textContent=loaded?state.tasks.length:'—';
  $('stat-resolved').textContent=loaded?state.issues.filter(i=>i.status==='RESOLVED').length:'—';
  renderIssues(); renderDetail(); renderMessages(); updateControls();
}
function renderIssues() {
  if(state.issues===null) {$('issues-list').innerHTML=empty('正在连接并读取当前用户的问题…');return;}
  const filter=$('issue-filter').value;
  const issues=state.issues.filter(i=>filter==='ALL'||i.status===filter).sort((a,b)=>b.created_at-a.created_at);
  if(!issues.some(i=>i.id===state.selected)) state.selected=issues[0]?.id||null;
  $('issues-list').innerHTML=issues.length?issues.map(i=>{
    const value=i.issue_type==='DUPLICATE'?i.transactions?.[0]?.amount_fen:i.remaining_amount_fen;
    return `<button class="issue-card issue-item ${i.id===state.selected?'active':''}" data-issue="${esc(i.id)}"><div class="issue-top"><strong>${esc(names[i.issue_type])}</strong>${badge(i.status)}</div><div class="issue-amount">${esc(money(value))}<small>${i.issue_type==='DUPLICATE'?'每笔消费':'仍待到账'}</small></div><p>${esc(i.issue_type==='DUPLICATE'?(i.transactions?.[0]?.merchant_name||i.transactions?.[0]?.merchant_id||'同商户'):i.refund?.refund_no||'退款跟进')}</p><small>${esc(time(i.created_at))}</small></button>`;
  }).join(''):empty('当前筛选下没有问题。可在演示区输入一组模拟事件。');
}
function historySummary(history) {
  const task=state.tasks.find(t=>t.id===history.task_id);
  const type=task?`${names[task.kind]||task.kind} · `:'';
  const labels={ISSUE_CREATED:'发现符合规则的问题，已建立记录',NORMAL:'用户确认是两次正常购买',
    CONFIRM_PROBLEM:'用户确认有问题，等待事实或新的约定',RESOLVE:'用户确认已自行解决',CLOSE:'用户关闭跟进',
    CLOSE_NO_CHANGE:'重复关闭，保留原处理结论',REOPEN:'用户明确重新开启',SNOOZE:'用户约定了下次提醒',
    FULL_REFUND_VERIFIED:'根据到账记录验证足额，问题解决',NEW_ARRIVAL:'收到新的退款到账事实',
    FACT_ONLY_CLOSED:'关闭后收到新事实，仅补记录',CANCELLED:'取消尚未提交的提醒',
    TASK_CREATED:type+'已安排提醒',SUBMITTING:type+'已提交模拟通知',SENT:type+'已核实发送成功',
    UNKNOWN:type+'结果未知，等待查询核实',FAILED:type+'未成功，不自动重发'};
  return labels[history.result]||names[history.result]||history.result;
}
function renderDetail() {
  const issue=state.issues?.find(i=>i.id===state.selected);
  if(!issue) {$('detail-panel').innerHTML=empty('选择一条问题，查看交易、处理过程和结案依据。');return;}
  const active=!terminal(issue.status);
  const controls=[['NORMAL','两次正常购买',active&&issue.issue_type==='DUPLICATE'],
    ['CONFIRM_PROBLEM','确有问题',active&&issue.status!=='CONFIRMED_PROBLEM'],['SNOOZE','明天再提醒',active],
    ['RESOLVE','已自行解决',active],['CLOSE','关闭跟进',active],['REOPEN','重新开启',issue.status==='CLOSED']]
    .filter(x=>x[2]).map(([name,label])=>`<button class="btn ${name==='CLOSE'?'btn-danger':'btn-secondary'}" data-action="${name}">${label}</button>`).join('');
  const refund=issue.refund;
  const basis=issue.resolution_basis==='FULL_REFUND_VERIFIED'?'到账记录已验证足额':issue.resolution_basis==='USER_CONFIRMED'?'用户确认解决，未声称已验证退款':'尚无解决依据';
  const note=issue.paused?'当前用户已暂停，新增事实尚未评估。':issue.status==='CLOSED'?'此问题已关闭，新事实只补记录，不继续催办。':issue.status==='CONFIRMED_PROBLEM'?'已确认有问题，仍待解决；等待相关事实或你的下一次明确约定。':null;
  const facts=refund?`<div class="detail-grid"><div><small>应退金额</small><strong>${money(refund.expected_amount_fen)}</strong></div><div><small>累计到账</small><strong>${money(refund.received_amount_fen)}</strong></div><div><small>仍待到账</small><strong>${money(refund.remaining_amount_fen)}</strong></div></div>`:'';
  const transactions=issue.transactions?.length?`<div class="table-wrap"><table><thead><tr><th>交易编号</th><th>商户</th><th>金额</th><th>发生时间</th></tr></thead><tbody>${issue.transactions.map(t=>`<tr><td>${esc(t.tx_no)}</td><td>${esc(t.merchant_name||t.merchant_id)}</td><td>${money(t.amount_fen)}</td><td>${esc(time(t.occurred_at))}</td></tr>`).join('')}</tbody></table></div>`:empty('尚未提供关联消费交易，退款按退款编号归集。');
  const related=issue.related_issue_ids?.length?`<p class="muted">共享交易关联 ${issue.related_issue_ids.length} 条问题：${issue.related_issue_ids.map(id=>`<button class="link-button" data-related="${esc(id)}">${esc(id.slice(0,8))}</button>`).join(' ')}</p>`:'';
  const tasks=state.tasks.filter(t=>t.issue_id===issue.id);
  const timeline=[...(issue.history||[])].reverse();
  $('detail-panel').innerHTML=`<div class="detail-heading"><div><small>问题详情 · ${esc(issue.id.slice(0,8))}</small><h2>${esc(names[issue.issue_type])}</h2></div>${badge(issue.status)}</div><p class="reason">${esc(issue.reason)}</p>${note?`<div class="notice">${esc(note)}</div>`:''}${facts}<div class="kv-grid"><div><small>跟进方式</small><span>${esc(names[issue.followup_mode]||issue.followup_mode)}</span></div><div><small>自动追加次数</small><span>${issue.auto_followup_count} / 2 <small>首次及用户约定不计入</small></span></div><div><small>解决依据</small><span>${esc(basis)}</span></div>${issue.requested_remind_at?`<div><small>用户约定时间</small><span>${esc(time(issue.requested_remind_at))}</span></div>`:''}</div><div class="action-grid">${controls}</div><h3>相关交易</h3>${transactions}${related}<h3>提醒记录</h3>${tasks.length?`<div class="task-list">${tasks.map(t=>`<div class="task-row"><span>${esc(names[t.kind])}</span>${badge(t.status)}<small>${esc(time(t.sent_at??t.due_at))}</small></div>`).join('')}</div>`:empty('尚无提醒任务。')}<h3>处理时间线</h3><div class="timeline">${timeline.map(h=>`<div class="timeline-item"><small>${esc(time(h.processed_at))}</small><p>${esc(historySummary(h))}</p></div>`).join('')||empty('暂无处理记录。')}</div>`;
}
function renderMessages() {
  $('notifications-list').innerHTML=state.messages.length?[...state.messages].reverse().slice(0,8).map(m=>{
    const c=m.content||{};
    return `<div class="notification-card message-card"><div class="issue-top"><strong>模拟通知已投递</strong><small>${esc(time(m.delivered_at))}</small></div><p>${esc(c.message||'账单问题跟进提醒')}</p>${c.remaining_amount_fen!=null?`<strong>仍待到账 ${money(c.remaining_amount_fen)}</strong>`:''}<small>提醒 ${esc(m.task_id?.slice(0,8)||'')} · 接收用户 ${esc(m.recipient)}</small></div>`;
  }).join(''):empty(state.issues===null?'尚未加载消息。':'暂无成功投递的模拟通知。失败或未知结果请查看问题中的提醒记录。');
}
async function run(label,operation) {
  if(state.busy) return;
  state.busy=true;updateControls();
  try {
    const result=await operation();
    const suffix=result?.result?' · '+({APPLIED:'已处理',DEFERRED:'事实已保存，暂停中待评估',DUPLICATE:'已识别重复，未再次生效',CONFLICT:'内容冲突，已保留原文且未覆盖'}[result.result]||result.result):'';
    log(label+suffix+(result?.receipt_id?' · 接收记录 '+result.receipt_id.slice(0,8):''));
    notify(label+suffix);
    await refresh(true);
  } catch(error) {
    log(label+'：'+(error.status&&error.status<500?'业务未生效':'执行结果未确认')+' · '+error.message);
    notify(error.message,true);
    await refresh(true);
  } finally {state.busy=false;updateControls();}
}
async function financial(path,payload) {
  // 发送前保存这次原始意图；超时后可原样重放，不换编号盲目创建新事实。
  state.lastEvent={path,payload,token:state.token};
  return write(path,payload);
}
async function simulate(kind) {
  if(kind==='replay') {
    if(!state.lastEvent||state.lastEvent.token!==state.token) throw new Error('当前用户没有可重放事件。');
    return write(state.lastEvent.path,state.lastEvent.payload);
  }
  const health=await request('/health');
  const now=health.now_ms, group='page-'+eventId().slice(0,8);
  if(kind==='pair'||kind==='triple') {
    const offsets=kind==='pair'?[5,0]:[9,4,0];
    let result;
    for(let i=0;i<offsets.length;i++) result=await financial('/events/transactions',{
      event_id:eventId(),tx_no:group+'-'+(i+1),merchant_id:group+'-shop',merchant_name:'演示咖啡店',
      amount_fen:3000,occurred_at:iso(now-offsets[i]*60000)});
    return result;
  }
  if(kind==='refund') {
    $('refund-no').value=group+'-refund';
    return financial('/events/refund-confirmations',{event_id:eventId(),refund_no:$('refund-no').value,
      expected_amount_fen:10000,confirmed_at:iso(now)});
  }
  const refund=$('refund-no').value.trim();
  if(!refund) throw new Error('请先创建退款申请，或填入当前用户已有的退款编号。');
  return financial('/events/refund-arrivals',{event_id:eventId(),refund_no:refund,arrival_no:group+'-arrival',
    amount_fen:kind==='arrival60'?6000:4000,arrived_at:iso(now)});
}
$('connect-btn').addEventListener('click',connect);
$('user-select').addEventListener('change',()=>{$('user-token').value=$('user-select').value;connect();});
$('refresh-btn').addEventListener('click',()=>refresh(true));
$('refund-no').addEventListener('input',updateControls);
$('issue-filter').addEventListener('change',()=>{renderIssues();renderDetail();updateControls();});
$('issues-list').addEventListener('click',event=>{
  const button=event.target.closest('[data-issue]');
  if(button&&!state.busy) {state.selected=button.dataset.issue;renderIssues();renderDetail();updateControls();}
});
$('detail-panel').addEventListener('click',event=>{
  const related=event.target.closest('[data-related]');
  if(related&&!state.busy) {state.selected=related.dataset.related;$('issue-filter').value='ALL';render();return;}
  const button=event.target.closest('[data-action]');
  if(!button||state.busy) return;
  const name=button.dataset.action,id=state.selected;
  if(name==='CLOSE'&&!confirm('关闭后停止自动跟进，已有记录和消息仍保留。确认关闭？')) return;
  const labels={NORMAL:'确认两次正常购买',CONFIRM_PROBLEM:'确认有问题',SNOOZE:'约定明天再提醒',RESOLVE:'确认已自行解决',CLOSE:'关闭跟进',REOPEN:'明确重新开启'};
  run(labels[name],()=>write('/issues/'+encodeURIComponent(id)+'/actions',{
    event_id:eventId(),action:name,...(name==='SNOOZE'?{tomorrow:true}:{})}));
});
for(const button of document.querySelectorAll('[data-demo]')) button.addEventListener('click',()=>{
  const labels={pair:'创建两笔相近消费',triple:'创建三笔相近消费',refund:'创建100元退款申请',arrival60:'记录到账60元',arrival40:'记录到账40元',replay:'原样重放最近事件'};
  run(labels[button.dataset.demo],()=>simulate(button.dataset.demo));
});
for(const button of document.querySelectorAll('[data-hours]')) button.addEventListener('click',()=>{
  run('推进全局模拟时间 '+button.dataset.hours+' 小时',()=>write('/demo/clock/advance',{hours:Number(button.dataset.hours)},'POST',true));
});
for(const button of document.querySelectorAll('[data-paused]')) button.addEventListener('click',()=>{
  const paused=button.dataset.paused==='true';
  run(paused?'暂停当前用户跟进':'恢复当前用户跟进',()=>write('/me/settings',{event_id:eventId(),paused},'PUT'));
});
connect();
setInterval(()=>{if(!state.busy) refresh();},3000);