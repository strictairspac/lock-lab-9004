import {table} from '../components/common/table.js';
import {esc,date} from '../utils/html.js';
import {session} from '../hooks/session.js';

const unknown='<span class="unknown" title="历史申请没有受理依据">未知</span>';

function approvedCell(t){
  if(!t.approved_quantity_known) return unknown;
  return `<b class="quantity">${t.approved_quantity}</b> 份`;
}

function restoredCell(t){
  if(t.registered_quantity===null||t.registered_quantity===undefined) return unknown;
  const src=t.registered_quantity_source;
  const badge=t.restored_matches_approved?'<span class="same">与核准量一致</span>':'<span class="drift">与核准量不同</span>';
  return `<b>${t.registered_quantity}</b> 份 <small>${esc(src?.kind_label||'')}${src?.code?' · 补记 '+esc(src.code):''} · 生效 ${date(src?.effective_at)}</small>${badge}`;
}

export function transfersPage(items,samples){return `<div class="split"><section><div class="section-top"><h2>交接申请簿</h2><span>${items.length} 条 / 待复核输入</span></div>${table(['申请编号','样本','移交','受理核准量','按记录还原登记时数量','接收方'],items.map(t=>[`<strong>${esc(t.code)}</strong><small>${esc(t.note)}</small>`,esc(samples.find(x=>x.id===t.sample_id)?.code),`${t.quantity} 份`,approvedCell(t),restoredCell(t),esc(t.destination)]))}<div class="notice">受理核准量是登记那一刻的样本量快照，不随后续改量或补记变化；还原量按数量流水取登记时刻的最后一条生效记录。历史申请缺少依据时标为“未知”。</div></section><section class="form-panel"><span class="eyebrow">TRANSFER REQUEST</span><h2>新建流转申请</h2><form id="transfer-form"><label>申请编号<input name="code" placeholder="shift-20261117" required></label><label>样本批次<select name="sample_id">${samples.map(s=>`<option value="${s.id}" data-quantity="${s.quantity}">${esc(s.code)} · ${s.quantity} 份</option>`).join('')}</select></label><div class="fields"><label>移交份数<input name="quantity" type="number" min="1" value="30" required></label><label>接收实验室<input name="destination" value="理化检测室" required></label></div><label>交接备注<input name="note" value="09:15 交接班复核"></label><input type="hidden" name="approved_quantity"><p class="help">保存时以你看到的样本可用量受理；若期间发生改量，申请会被拒绝并要求按最新数量重提。</p><button class="primary" ${session.user.role==='viewer'?'disabled':''}>保存流转申请 +</button></form></section></div>`;}
