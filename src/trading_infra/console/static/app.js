'use strict';

const app = {inventory: null, fees: null, operator: null, editing: null, results: {}, error: ''};

const AUTH_FIELDS = {
  evm_key: ['private_key'],
  evm_key_address: ['private_key', 'account_address'],
  hmac: ['api_key', 'secret'],
  hmac_passphrase: ['api_key', 'secret', 'passphrase'],
};
const FIELD_LABELS = {
  private_key: '私钥', account_address: '账户地址',
  api_key: 'API Key', secret: 'Secret', passphrase: 'Passphrase',
};
const SECRET_FIELDS = new Set(['private_key', 'api_key', 'secret', 'passphrase']);

const esc = value => String(value ?? '').replace(/[&<>"']/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  if (response.status === 401) { location.replace('/login.html'); throw new Error('需要登录'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${path} 失败`);
  return data;
}

const badge = (kind, text) => `<span class="badge ${kind}">${esc(text)}</span>`;
const bps = rate => rate === null || rate === undefined ? '—'
  : `${(Number(rate) * 10000).toFixed(2)} bp`;
const when = seconds => !seconds ? '从未'
  : new Date(Number(seconds) * 1000).toLocaleString('zh-CN', {hour12: false});

function accountRow(row) {
  const open = app.editing === row.key;
  const state = !row.configured ? badge('missing', '未填写')
    : row.ready ? badge('ready', '已就绪') : badge('partial', '不完整');
  const fields = (AUTH_FIELDS[row.auth] || []).map(name => `
    <label class="field">${esc(FIELD_LABELS[name] || name)}
      <input name="${esc(name)}" autocomplete="off"
             type="${SECRET_FIELDS.has(name) ? 'password' : 'text'}"
             placeholder="${row.configured ? '已保存，留空保持不变' : '填写后保存'}"></label>`).join('');
  const result = app.results[row.key];
  const detail = open ? `<tr class="detail"><td colspan="6">
    <form class="account-form" data-key="${esc(row.key)}"
          data-venue="${esc(row.venue)}" data-label="${esc(row.label)}">
      ${fields}
      <div class="form-footer">
        <button class="primary" type="submit">保存</button>
        <button class="ghost" type="button" data-test="${esc(row.key)}">验证这把 Key</button>
        ${row.probeable ? `<button class="ghost" type="button" data-probe="${esc(row.key)}">读取账户费率</button>` : ''}
        ${row.configured ? `<button class="ghost" type="button" data-delete="${esc(row.key)}">删除</button>` : ''}
      </div>
      ${result ? `<p class="result ${result.ok ? 'positive' : 'negative'}">${esc(result.message)}</p>` : ''}
    </form></td></tr>` : '';
  return `<tr>
    <td class="mono">${esc(row.key)}</td>
    <td>${esc(row.used_by)}</td>
    <td>${esc(row.purpose)}</td>
    <td>${state}${row.required && !row.ready ? ' <span class="hint">必需</span>' : ''}</td>
    <td>${row.error ? `<span class="negative">${esc(row.error)}</span>` : ''}</td>
    <td class="row-actions">
      <button class="ghost" data-edit="${esc(row.key)}">${open ? '收起' : '填写'}</button>
    </td></tr>${detail}`;
}

function accountsPanel() {
  const data = app.inventory;
  if (!data) return `<section class="panel"><p class="subtle">正在读取账户…</p></section>`;
  if (data.error) return `<section class="panel">
    <div class="panel-head"><h2>交易所账户</h2><span class="spacer"></span>${badge('missing', '读取失败')}</div>
    <p class="negative">${esc(data.error)}</p></section>`;
  const probeable = new Set((app.fees?.fees || []).filter(f => f.probeable).map(f => f.venue));
  const rows = data.accounts.map(row => accountRow({...row, probeable: probeable.has(row.venue)})).join('');
  const missing = data.accounts.filter(row => row.required && !row.ready).length;
  const archived = data.archived || [];
  return `<section class="panel">
    <div class="panel-head"><h2>交易所账户</h2><span class="spacer"></span>
      ${missing ? badge('missing', `${missing} 项必需未就绪`) : badge('ready', '必需项齐备')}</div>
    <div class="table-scroll"><table>
      <thead><tr><th>账户</th><th>谁在用</th><th>用途</th><th>状态</th><th>问题</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${archived.length ? `<p class="negative">存储里还有 ${archived.length} 个无代码引用的账户：${esc(archived.map(a => a.key).join('、'))}</p>` : ''}
    <p class="subtle">写入 <code>${esc(data.store)}</code>。Grid 和 arb 只读这份文件，填一次两边生效。</p>
  </section>`;
}

function feesPanel() {
  const data = app.fees;
  if (!data) return '';
  const rows = data.fees.map(row => {
    const real = row.account_taker !== undefined && row.account_taker !== null;
    return `<tr>
      <td class="mono">${esc(row.venue)}${row.network === 'mainnet' ? '' : ` · ${esc(row.network)}`}</td>
      <td>${bps(row.published_maker)} / ${bps(row.published_taker)}</td>
      <td>${real ? `<span class="positive">${bps(row.account_maker)} / ${bps(row.account_taker)}</span>` : '<span class="subtle">未读取</span>'}</td>
      <td class="subtle">${real ? esc(when(row.probed_at)) : row.probeable ? '可读取' : '该所无账户档位'}</td>
    </tr>`;
  }).join('');
  return `<section class="panel">
    <div class="panel-head"><h2>费率档位</h2><span class="spacer"></span>
      ${badge('', `${data.fees.filter(f => f.account_taker).length} / ${data.fees.length} 已读取真实档位`)}</div>
    <div class="table-scroll"><table>
      <thead><tr><th>场所</th><th>公开入门档 maker/taker</th><th>账户实际档位</th><th>读取时间</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="subtle">公开档是各所对任何人的最差费率，用它定价只会低估edge。读到真实档位后，Grid 的跨所台和 arb 的期现台立即按真实成本计算——存的是一个数字，不是密钥。</p>
  </section>`;
}

function operatorPanel() {
  const data = app.operator || {};
  return `<section class="panel">
    <div class="panel-head"><h2>凭证台登录</h2><span class="spacer"></span>${badge('', data.user || '未配置')}</div>
    <form id="operator-form" class="account-form">
      <label class="field">操作员账号<input name="user" value="${esc(data.user || '')}" autocomplete="username" required></label>
      <label class="field">新密码<input name="password" type="password" autocomplete="new-password" placeholder="至少 8 位" required></label>
      <div class="form-footer"><button class="primary" type="submit">更新登录</button>
        <span class="result" id="operator-result"></span></div>
    </form>
    <p class="subtle">存于 <code>${esc(data.auth_file || '')}</code>，0600 root，不属 dmechai-secrets 组——能读密钥的进程读不到这个文件。</p>
  </section>`;
}

function render() {
  document.getElementById('operator').textContent = app.operator?.user || '';
  document.getElementById('root').innerHTML =
    (app.error ? `<p class="negative">${esc(app.error)}</p>` : '') +
    accountsPanel() + feesPanel() + operatorPanel();
}

async function refresh() {
  try {
    const [inventory, fees, operator] = await Promise.all([
      api('/api/inventory'), api('/api/fees'), api('/api/operator')]);
    Object.assign(app, {inventory, fees, operator, error: ''});
  } catch (err) {
    app.error = err.message;
  }
  render();
}

async function act(label, work) {
  app.error = '';
  try {
    await work();
  } catch (err) {
    app.error = `${label}：${err.message}`;
  }
  render();
}

document.addEventListener('click', async event => {
  const target = event.target.closest('[data-edit],[data-test],[data-probe],[data-delete]');
  if (target) {
    const {edit, test, probe, delete: remove} = target.dataset;
    if (edit) { app.editing = app.editing === edit ? null : edit; render(); return; }
    const key = test || probe || remove;
    const [venue, label] = key.split(':');
    target.disabled = true;
    if (test) {
      await act('验证失败', async () => {
        app.results[key] = await api('/api/account/test', {venue, label});
      });
    } else if (probe) {
      await act('读取费率失败', async () => {
        const data = await api('/api/fees/probe', {venue, label});
        app.results[key] = {ok: data.ok, message: data.message};
        app.fees = {store: data.store, fees: data.fees};
      });
    } else if (remove) {
      if (!confirm(`删除 ${key}？存储里的私钥会被永久销毁，无法恢复。`)) {
        target.disabled = false; return;
      }
      await act('删除失败', async () => {
        app.inventory = await api('/api/account', {venue, label, delete: true});
        app.editing = null;
        delete app.results[key];
      });
    }
    return;
  }
  if (event.target.id === 'logout') {
    await api('/api/logout', {}).catch(() => {});
    location.replace('/login.html');
  }
});

document.addEventListener('submit', async event => {
  const form = event.target;
  if (form.classList.contains('account-form') && form.dataset.key) {
    event.preventDefault();
    const fields = {};
    for (const [name, value] of new FormData(form)) {
      if (String(value).trim()) fields[name] = String(value).trim();
    }
    if (!Object.keys(fields).length) {
      app.results[form.dataset.key] = {ok: false, message: '没有填写任何字段'};
      render(); return;
    }
    await act('保存失败', async () => {
      app.inventory = await api('/api/account',
        {venue: form.dataset.venue, label: form.dataset.label, fields});
      app.results[form.dataset.key] = {ok: true, message: '已保存，Grid 和 arb 立即可用'};
    });
    return;
  }
  if (form.id === 'operator-form') {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form));
    try {
      app.operator = {...app.operator, ...await api('/api/operator', payload)};
      document.getElementById('operator-result').textContent = '已更新';
      document.getElementById('operator-result').className = 'result positive';
      form.querySelector('[name=password]').value = '';
    } catch (err) {
      document.getElementById('operator-result').textContent = err.message;
      document.getElementById('operator-result').className = 'result negative';
    }
  }
});

refresh();
