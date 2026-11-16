import React, { useState, useEffect, useCallback, useRef, createContext, useContext } from 'react'
import { Routes, Route, NavLink, Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { Home, Users, PlusCircle, Scale, Bell, Trophy, CalendarDays, History, UserRound, CheckCheck, LogOut, ArrowRightLeft } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

const Ctx = createContext()
const useApp = () => useContext(Ctx)
const inr = n => '₹' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })
const ago = iso => new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
async function api(path, opts = {}) {
  const t = localStorage.getItem('fs_token')
  const r = await fetch('/api' + path, { method: opts.method || 'GET', headers: { 'Content-Type': 'application/json', ...(t && { Authorization: 'Bearer ' + t }) }, body: opts.body && JSON.stringify(opts.body) }).catch(() => { throw new Error('Cannot reach the server. Is the backend running?') })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) { if (r.status === 401 && t && !/^\/(login|register)/.test(path)) { localStorage.removeItem('fs_token'); location.href = '/' } throw new Error(typeof d.detail === 'string' ? d.detail : d.detail?.[0]?.msg || 'Something went wrong.') }
  return d
}
function useLoad(path, deps = []) {
  const [data, setData] = useState(null), [err, setErr] = useState(''), seq = useRef(0)
  const load = useCallback(() => { const id = ++seq.current; return api(path).then(d => { if (id === seq.current) { setData(d); setErr('') } }).catch(e => { if (id === seq.current) setErr(e.message) }) }, [path])
  useEffect(() => { setData(null); setErr(''); load() }, [load, ...deps]) // eslint-disable-line
  return [data, load, err]
}
const Loading = ({ err }) => <div className="card mute">{err || 'Loading…'}</div>
const Av = ({ u }) => <div className="av" title={u?.name}>{u?.avatar || '🙂'}</div>
const Bar_ = ({ pct, ok }) => <div className={'bar' + (ok ? ' ok' : '')}><i style={{ width: pct + '%' }} /></div>
const Empty = ({ icon = '🦁', text, to, cta }) => <div className="card" style={{ textAlign: 'center' }}><div style={{ fontSize: '2.4rem' }}>{icon}</div><p className="mute">{text}</p>{to && <Link className="btn" to={to}>{cta}</Link>}</div>

function Confetti({ show }) {
  if (!show) return null
  return <div className="confetti">{Array.from({ length: 36 }, (_, i) => <span key={i} style={{ left: Math.random() * 100 + '%', animationDelay: Math.random() * .8 + 's' }}>{['🎉', '💸', '✨', '🦁'][i % 4]}</span>)}</div>
}

export default function App() {
  const [user, setUser] = useState(null), [ready, setReady] = useState(false), [toast, setToast] = useState(null), [boom, setBoom] = useState(false), [unread, setUnread] = useState(0)
  const say = (text, err) => { setToast({ text, err }); setTimeout(() => setToast(null), 3500) }
  const celebrate = () => { setBoom(true); setTimeout(() => setBoom(false), 3500) }
  const refreshUnread = useCallback(() => api('/notifications').then(n => setUnread(n.filter(x => !x.read).length)).catch(() => {}), [])
  useEffect(() => { if (localStorage.getItem('fs_token')) api('/me').then(setUser).catch(() => {}).finally(() => setReady(true)); else setReady(true) }, [])
  useEffect(() => { if (user) { refreshUnread(); const i = setInterval(refreshUnread, 15000); return () => clearInterval(i) } }, [user, refreshUnread])
  if (!ready) return null
  const logout = () => { localStorage.removeItem('fs_token'); setUser(null) }
  const value = { user, setUser, say, celebrate, refreshUnread, logout }
  return <Ctx.Provider value={value}>
    {user ? <Shell unread={unread} /> : <Auth />}
    {toast && <div className={'toast' + (toast.err ? ' err' : '')} role="status">{toast.text}</div>}
    <Confetti show={boom} />
  </Ctx.Provider>
}

const NAV = [['/', 'Home', Home], ['/groups', 'Groups', Users], ['/expense/new', 'Add', PlusCircle], ['/who', 'Who owes whom', ArrowRightLeft], ['/approvals', 'Approvals', CheckCheck], ['/history', 'History', History], ['/achievements', 'Rewards', Trophy], ['/weekly', 'Weekly', CalendarDays], ['/profile', 'Profile', UserRound]]
function Shell({ unread }) {
  const { user } = useApp()
  const cls = ({ isActive }) => 'nav' + (isActive ? ' on' : '')
  return <div className="shell">
    <aside className="side"><div className="brand">FairSplit</div><div className="tag">Split Smart. Settle Easy. Stay Fair.</div>
      {NAV.map(([to, label, I]) => <NavLink key={to} to={to} end={to === '/'} className={cls}><I size={18} />{label}</NavLink>)}</aside>
    <div className="main">
      <div className="top"><Link to="/notifications" className="bell" aria-label="Notifications"><Bell size={18} />{unread > 0 && <span className="dot">{unread}</span>}</Link>
        <Link to="/profile" className="me"><Av u={user} />{user.name}</Link></div>
      <div className="page"><Routes>
        <Route path="/" element={<Dashboard />} /><Route path="/groups" element={<Groups />} /><Route path="/groups/new" element={<NewGroup />} /><Route path="/groups/:id" element={<GroupPage />} />
        <Route path="/expense/new" element={<AddExpense />} /><Route path="/balances" element={<Who />} /><Route path="/who" element={<Who />} />
        <Route path="/approvals" element={<Approvals />} /><Route path="/notifications" element={<Notifications />} /><Route path="/achievements" element={<Rewards />} />
        <Route path="/weekly" element={<Weekly />} /><Route path="/history" element={<Hist />} /><Route path="/profile" element={<Profile />} /><Route path="*" element={<Navigate to="/" />} />
      </Routes></div>
    </div>
    <nav className="bottom">{[NAV[0], NAV[1], NAV[2], NAV[4], NAV[8]].map(([to, label, I]) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => isActive ? 'on' : ''}><I size={20} />{label}</NavLink>)}</nav>
  </div>
}

function Auth() {
  const { setUser, say } = useApp()
  const [reg, setReg] = useState(false), [f, setF] = useState({ name: '', email: '', password: '' }), [demo, setDemo] = useState([]), [busy, setBusy] = useState(false)
  useEffect(() => { api('/demo-users').then(setDemo).catch(() => {}) }, [])
  const go = async (body, path) => { setBusy(true); try { const d = await api(path, { method: 'POST', body }); localStorage.setItem('fs_token', d.token); setUser(d.user) } catch (e) { say(e.message, true) } setBusy(false) }
  return <div className="auth"><div className="card">
    <div className="brand" style={{ padding: 0, fontSize: '2rem' }}>FairSplit</div><p className="mute">Split Smart. Settle Easy. Stay Fair.</p>
    {reg && <><label>Name</label><input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} /></>}
    <label>Email</label><input type="email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} />
    <label>Password</label><input type="password" value={f.password} onChange={e => setF({ ...f, password: e.target.value })} />
    <button className="btn" disabled={busy} onClick={() => go(f, reg ? '/register' : '/login')}>{reg ? 'Create account' : 'Sign in'}</button>{' '}
    <button className="btn ghost" onClick={() => setReg(!reg)}>{reg ? 'I have an account' : 'Register'}</button>
    {!reg && demo.length > 0 && <><p className="mute" style={{ marginTop: 20 }}>Try a demo account (password demo123)</p>
      <div className="demo">{demo.map(u => <button key={u.id} onClick={() => go({ email: u.email, password: u.password }, '/login')}>{u.avatar} {u.name}</button>)}</div></>}
  </div></div>
}

function PayModal({ group_id, creditor, max, onClose, onDone }) {
  const { say } = useApp()
  const [f, setF] = useState({ amount: max, method: 'UPI', ref: '' }), [busy, setBusy] = useState(false)
  const submit = async () => { setBusy(true); try { await api('/payments', { method: 'POST', body: { group_id, creditor_id: creditor.id, ...f, amount: String(f.amount) } }); say(`Sent to ${creditor.name} for approval. Your debt updates once they approve.`); onDone(); onClose() } catch (e) { say(e.message, true) } setBusy(false) }
  return <div className="modal" onClick={onClose}><div className="card" onClick={e => e.stopPropagation()}>
    <h2>Paid {creditor.name}?</h2><p className="mute">Nothing is settled until {creditor.name} approves.</p>
    <label>Amount paid (₹)</label><input type="number" min="0" step="0.01" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} />
    <label>Payment method</label><select value={f.method} onChange={e => setF({ ...f, method: e.target.value })}>{['UPI', 'Cash', 'Bank Transfer', 'Other'].map(m => <option key={m}>{m}</option>)}</select>
    <label>Transaction reference (optional)</label><input value={f.ref} onChange={e => setF({ ...f, ref: e.target.value })} />
    <div className="row"><button className="btn" disabled={busy} onClick={submit}>Send for approval</button><button className="btn ghost" onClick={onClose}>Cancel</button></div>
  </div></div>
}

function Dashboard() {
  const [d, load, err] = useLoad('/dashboard'), [pay, setPay] = useState(null), { say } = useApp()
  if (!d) return <Loading err={err} />
  const t = d.totals
  return <>
    <div className="hero"><div><h1>Hi {d.user.name} 👋</h1><p>{t.debt_free ? 'Debt-free. Tycoon Tiger salutes you 🦁' : `${inr(t.owe)} to settle · ${t.pct}% cleared`}</p></div>
      <div className="row wrap"><Link className="btn ghost" to="/expense/new">Add expense</Link><Link className="btn ghost" to="/weekly">View weekly summary</Link></div></div>
    <div className="grid g4">
      <div className="card stat"><span>You owe</span><b className="amt owe">{inr(t.owe)}</b></div>
      <div className="card stat"><span>Others owe you</span><b className="amt get">{inr(t.owed)}</b></div>
      <div className="card stat"><span>Settled so far</span><b>{inr(t.settled)}</b></div>
      <div className="card stat"><span>Pending payments</span><b>{t.pending_payments}</b>{t.to_approve > 0 && <Link to="/approvals">{t.to_approve} waiting for your approval</Link>}</div>
    </div>
    <div className="card"><div className="row sp"><h2>Your progress</h2><b>{t.debt_free ? 'Debt Free 🎉' : t.pct + '%'}</b></div>
      <Bar_ pct={t.pct} ok={t.debt_free} /><p className="mute">{inr(t.settled)} settled · {inr(t.owe)} remaining · 🔥 {d.user.streak}-week streak · {d.user.xp} XP</p></div>
    <div className="grid g2">
      <div className="card"><h2>Who owes whom</h2>
        {d.who.length === 0 ? <p className="mute">Nobody owes anything. Lovely.</p> : d.who.map((w, i) => <div className="item" key={i}><Av u={w.other} />
          <div className="grow"><b>{w.other.name}</b><div className="mute">{w.group}{w.awaiting > 0 && ' · ' + inr(w.awaiting) + ' awaiting approval'}</div></div>
          <div style={{ textAlign: 'right' }}><div className={'amt ' + (w.owe ? 'owe' : 'get')}>{w.owe ? 'You owe ' : 'Owes you '}{inr(Math.abs(w.net))}</div>
            {w.owe && w.overdue && <span className="pill bad">Ghost Baba watchlist 👻</span>}
            {w.owe && w.payable > 0 && <div><button className="btn sm" style={{ marginTop: 4 }} onClick={() => setPay({ group_id: w.group_id, creditor: w.other, max: w.payable })}>I've paid</button></div>}</div></div>)}</div>
      <div className="card"><h2>Upcoming reminders</h2>{d.reminders.length === 0 ? <p className="mute">No payments coming up.</p> : d.reminders.map((r, i) =>
        <div className="item" key={i}><div className="grow"><b>{r.title}</b><div className="mute">Pay {r.to} by {r.due}</div></div><span className={'pill ' + (r.overdue ? 'bad' : 'wait')}>{inr(r.amount)}</span></div>)}
        <h2 style={{ marginTop: 16 }}>Recent</h2>{d.recent.length === 0 ? <p className="mute">Nothing yet.</p> : d.recent.map((h, i) => <div className="item" key={i}><div className="grow">{h.title}<div className="mute">{h.group} · {h.date}</div></div><span className={'pill ' + h.status}>{h.status}</span></div>)}</div>
    </div>
    {pay && <PayModal {...pay} onClose={() => setPay(null)} onDone={load} />}
  </>
}

function Groups() {
  const [g, , err] = useLoad('/groups')
  if (!g) return <Loading err={err} />
  return <><div className="row sp wrap"><h1>My groups</h1><Link className="btn" to="/groups/new">Create group</Link></div>
    {g.length === 0 ? <Empty text="No groups yet. Create one and add your friends." to="/groups/new" cta="Create group" /> :
      <div className="grid g2">{g.map(x => <Link key={x.id} to={'/groups/' + x.id} className="card" style={{ color: 'inherit' }}><h2>{x.name}</h2>
        <div className="row">{x.members.map(m => <Av key={m.id} u={m} />)}</div><p className="mute">{inr(x.total_expenses)} spent · since {x.created}</p>
        <Bar_ pct={x.progress.pct} ok={x.progress.pct === 100} /><div className="mute">Group progress {x.progress.pct}%</div></Link>)}</div>}</>
}

function NewGroup() {
  const nav = useNavigate(), { say } = useApp(), [name, setName] = useState(''), [m, setM] = useState('')
  const go = async () => { try { const g = await api('/groups', { method: 'POST', body: { name, members: m.split(/[,\s]+/).filter(Boolean) } }); say('Group created.'); nav('/groups/' + g.id) } catch (e) { say(e.message, true) } }
  return <div className="card"><h1>Create group</h1><label>Group name</label><input value={name} onChange={e => setName(e.target.value)} />
    <label>Members (emails or user IDs, comma separated)</label><input placeholder="meera@demo.com, 3" value={m} onChange={e => setM(e.target.value)} />
    <button className="btn" onClick={go}>Create group</button></div>
}

function GroupPage() {
  const { id } = useParams(), [g, load, err] = useLoad('/groups/' + id), { user, say } = useApp(), nav = useNavigate(), [pay, setPay] = useState(null)
  if (!g) return <Loading err={err} />
  const by = g.people
  const leave = async () => { if (!window.confirm('Leave this group?')) return; try { await api(`/groups/${id}/leave`, { method: 'POST' }); say('You left the group.'); nav('/groups') } catch (e) { say(e.message, true) } }
  return <>
    <div className="row sp wrap"><div><h1>{g.name}</h1><span className="mute">Created {g.created} · {inr(g.total_expenses)} total</span></div>
      <div className="row"><Link className="btn" to={'/expense/new?group=' + id}>Add expense</Link><button className="btn ghost" onClick={leave}>Leave</button></div></div>
    <div className="card"><div className="row sp"><h2>Group progress</h2><b>{g.progress.pct}%</b></div><Bar_ pct={g.progress.pct} ok={g.progress.pct === 100} />
      <p className="mute">{inr(g.progress.settled)} confirmed of {inr(g.progress.total)} payable</p></div>
    <div className="card"><h2>Members</h2>{g.members.map(m => { const b = g.balances[m.id]; return <div className="item" key={m.id}><Av u={m} /><div className="grow"><b>{m.name}</b><Bar_ pct={b.pct} ok={b.debt_free} />
      <div className="mute">{b.debt_free ? 'Debt Free' : `${b.pct}% · ${inr(b.settled)} settled · ${inr(b.owe)} left`}</div></div></div> })}</div>
    <div className="card"><h2>Suggested settlements ({g.suggestions.length})</h2><p className="mute">Budget Baba's simplest route. Suggestions only; payments count once the receiver approves.</p>
      {g.suggestions.length === 0 ? <p className="mute">All square 🎉</p> : g.suggestions.map((s, i) => <div className="sug row sp" key={i}><span>🧮 {by[s.from].name} pays {by[s.to].name} <b>{inr(s.amount)}</b></span>
        {s.from === user.id && (s.payable > 0 ? <button className="btn sm" onClick={() => setPay({ group_id: g.id, creditor: by[s.to], max: s.payable })}>I've paid</button> : <span className="mute">Pay what you owe directly</span>)}</div>)}</div>
    <div className="card"><h2>Expenses</h2>{g.expenses.length === 0 ? <p className="mute">No expenses yet.</p> : g.expenses.map(e => <div className="item" key={e.id}><div className="grow"><b>{e.title}</b>
      <div className="mute">{by[e.payer_id]?.name} paid · {e.category} · {e.day} · {e.split} split</div>
      <div className="mute">{e.shares.map(s => `${by[s.debtor_id]?.name} ${inr(s.amount)}`).join(', ')}</div></div><b>{inr(e.amount)}</b></div>)}</div>
    {pay && <PayModal {...pay} onClose={() => setPay(null)} onDone={load} />}
  </>
}

const CATS = ['Food', 'Travel', 'Rent', 'Groceries', 'Shopping', 'Utilities', 'Others']
function AddExpense() {
  const { user, say } = useApp(), nav = useNavigate(), [groups] = useLoad('/groups')
  const qs = new URLSearchParams(location.search)
  const [f, setF] = useState({ group_id: '', title: '', amount: '', payer_id: user.id, category: 'Food', day: new Date().toISOString().slice(0, 10), description: '' })
  const [sel, setSel] = useState([]), [mode, setMode] = useState('equal'), [cust, setCust] = useState({}), [busy, setBusy] = useState(false)
  const g = groups?.find(x => x.id === Number(f.group_id))
  useEffect(() => { if (groups?.length && !f.group_id) pick(qs.get('group') || groups[0].id) }, [groups]) // eslint-disable-line
  const pick = id => { const x = groups.find(y => y.id === Number(id)) || groups[0]; setF(p => ({ ...p, group_id: x.id })); setSel(x.members.map(m => m.id)); setCust({}) }
  if (!groups) return <Loading />
  if (!groups.length) return <Empty text="Create a group before adding expenses." to="/groups/new" cta="Create group" />
  const cents = Math.round(Number(f.amount || 0) * 100), n = sel.length, base = n ? Math.floor(cents / n) : 0
  const shareOf = i => (i === sel[0] ? cents - base * (n - 1) : base) / 100   // same rounding as the server: leftover paise go to the first person
  const sumC = sel.reduce((a, i) => a + Math.round(Number(cust[i] || 0) * 100), 0), sum = sumC / 100
  const submit = async () => {
    if (!/^(?=.)[0-9]{0,8}(\.[0-9]{1,2})?$/.test(String(f.amount).trim())) return say('Enter a valid amount (numbers only, up to 2 decimals).', true)
    if (!sel.length) return say('Select at least one person to split with.', true)
    if (mode === 'custom' && sumC !== cents) return say(`Custom amounts must add up to exactly ${inr(f.amount)}.`, true)
    setBusy(true)
    try { await api('/expenses', { method: 'POST', body: { ...f, participants: sel, custom: mode === 'custom' ? Object.fromEntries(sel.map(i => [i, String(cust[i] || 0)])) : null } }); say('Expense added. Members have been notified.'); nav('/groups/' + f.group_id) }
    catch (e) { say(e.message, true) } setBusy(false)
  }
  return <div className="card"><h1>Add expense</h1>
    <label>Group</label><select value={f.group_id} onChange={e => pick(e.target.value)}>{groups.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
    <label>Title</label><input value={f.title} onChange={e => setF({ ...f, title: e.target.value })} />
    <div className="row wrap"><div className="grow"><label>Amount (₹)</label><input type="number" min="0" step="0.01" value={f.amount} onChange={e => setF({ ...f, amount: e.target.value })} /></div>
      <div className="grow"><label>Date</label><input type="date" value={f.day} onChange={e => setF({ ...f, day: e.target.value })} /></div></div>
    <div className="row wrap"><div className="grow"><label>Paid by</label><select value={f.payer_id} onChange={e => setF({ ...f, payer_id: Number(e.target.value) })}>{g?.members.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></div>
      <div className="grow"><label>Category</label><select value={f.category} onChange={e => setF({ ...f, category: e.target.value })}>{CATS.map(c => <option key={c}>{c}</option>)}</select></div></div>
    <label>Description</label><textarea rows="2" value={f.description} onChange={e => setF({ ...f, description: e.target.value })} />
    <div className="row sp"><label>Split between</label><div className="tabs"><button className={mode === 'equal' ? 'on' : ''} onClick={() => setMode('equal')}>Equal</button> <button className={mode === 'custom' ? 'on' : ''} onClick={() => setMode('custom')}>Custom</button></div></div>
    {g?.members.map(m => <div className="item" key={m.id}><input type="checkbox" style={{ width: 20, margin: 0 }} checked={sel.includes(m.id)} onChange={() => setSel(s => s.includes(m.id) ? s.filter(i => i !== m.id) : [...s, m.id])} /><Av u={m} /><span className="grow">{m.name}{m.id === f.payer_id && <span className="mute"> (paid, owes nothing)</span>}</span>
      {sel.includes(m.id) && (mode === 'equal' ? <b>{inr(shareOf(m.id))}</b> : <input type="number" min="0" step="0.01" style={{ width: 110, margin: 0 }} value={cust[m.id] ?? ''} onChange={e => setCust({ ...cust, [m.id]: e.target.value })} />)}</div>)}
    {mode === 'custom' && <p className={sumC === cents ? 'mute' : 'amt owe'}>Entered {inr(sum)} of {inr(f.amount)}{sumC !== cents && ` (${sumC > cents ? 'over' : 'short'} by ${inr(Math.abs(sumC - cents) / 100)})`}</p>}
    <button className="btn" disabled={busy || (mode === 'custom' && sumC !== cents)} onClick={submit}>Add expense</button></div>
}

function Who() {
  const [d, , err] = useLoad('/dashboard'), [groups] = useLoad('/groups')
  const [det, setDet] = useState({})
  useEffect(() => { groups?.forEach(g => api('/groups/' + g.id).then(x => setDet(p => ({ ...p, [g.id]: x }))).catch(() => {})) }, [groups])
  if (!d || !groups) return <Loading err={err} />
  return <><h1>Who owes whom</h1>
    <div className="card"><h2>Your balances</h2>{d.who.length === 0 ? <p className="mute">All square.</p> : d.who.map((w, i) => <div className="item" key={i}><Av u={w.other} /><div className="grow"><b>{w.other.name}</b><div className="mute">{w.group}</div></div><span className={'amt ' + (w.owe ? 'owe' : 'get')}>{w.owe ? 'You owe ' : 'Owes you '}{inr(Math.abs(w.net))}</span><span className={'pill ' + (w.awaiting ? 'wait' : w.overdue ? 'bad' : 'pending')}>{w.awaiting ? 'Awaiting confirmation' : w.overdue ? 'Overdue' : 'Pending'}</span></div>)}</div>
    {groups.map(g => { const x = det[g.id]; if (!x) return null; const by = x.people; return <div className="card" key={g.id}><h2>{g.name}: smart settlement</h2>
      <p className="mute">{x.suggestions.length} suggested transaction{x.suggestions.length === 1 ? '' : 's'}. Suggestions, not payments.</p>
      {x.suggestions.map((s, i) => <div className="sug" key={i}>🧮 {by[s.from].name} → {by[s.to].name}: <b>{inr(s.amount)}</b></div>)}</div> })}</>
}

function Approvals() {
  const [p, load, err] = useLoad('/payments'), { user, say, celebrate, refreshUnread } = useApp(), [rej, setRej] = useState(null), [reason, setReason] = useState(''), [busy, setBusy] = useState(false)
  if (!p) return <Loading err={err} />
  const inbox = p.filter(x => x.creditor.id === user.id && x.status === 'awaiting'), rest = p.filter(x => !inbox.includes(x))
  const approve = async x => { if (busy) return; setBusy(true); try { const r = await api(`/payments/${x.id}/approve`, { method: 'POST' }); say(`Approved ${inr(x.amount)}. ${inr(r.remaining)} still pending from ${x.debtor.name}.`); if (r.debt_free) celebrate(); load(); refreshUnread() } catch (e) { say(e.message, true); load() } setBusy(false) }
  const reject = async () => { if (busy) return; setBusy(true); try { await api(`/payments/${rej.id}/reject`, { method: 'POST', body: { reason } }); say('Rejected. The debt stays open.'); setRej(null); setReason(''); load() } catch (e) { say(e.message, true); load() } setBusy(false) }
  return <><h1>Payment approvals</h1>
    <div className="card"><h2>Waiting for you</h2>{inbox.length === 0 ? <p className="mute">Nothing to approve right now.</p> : inbox.map(x => <div className="item" key={x.id}><Av u={x.debtor} /><div className="grow"><b>{x.debtor.name} paid {inr(x.amount)}</b><div className="mute">{x.group} · {x.method}{x.ref && ' · ' + x.ref} · {ago(x.created)}</div></div>
      <button className="btn green sm" disabled={busy} onClick={() => approve(x)}>Approve</button><button className="btn red sm" disabled={busy} onClick={() => setRej(x)}>Reject</button></div>)}</div>
    <div className="card"><h2>All payment requests</h2>{rest.length === 0 ? <p className="mute">No other requests.</p> : rest.map(x => <div className="item" key={x.id}><div className="grow"><b>{x.debtor.name} → {x.creditor.name} · {inr(x.amount)}</b><div className="mute">{x.group} · {x.method} · {ago(x.created)}{x.reason && ' · Reason: ' + x.reason}</div></div><span className={'pill ' + x.status}>{x.status === 'awaiting' ? 'Awaiting Receiver Confirmation' : x.status}</span></div>)}</div>
    {rej && <div className="modal"><div className="card"><h2>Reject {inr(rej.amount)} from {rej.debtor.name}?</h2><label>Reason</label><input maxLength={200} value={reason} onChange={e => setReason(e.target.value)} /><div className="row"><button className="btn red" disabled={busy} onClick={reject}>Reject payment</button><button className="btn ghost" onClick={() => setRej(null)}>Cancel</button></div></div></div>}</>
}

function Notifications() {
  const [n, load, err] = useLoad('/notifications'), { refreshUnread } = useApp()
  if (!n) return <Loading err={err} />
  const mark = async id => { await api(id ? `/notifications/${id}/read` : '/notifications/read', { method: 'POST' }); load(); refreshUnread() }
  return <><div className="row sp"><h1>Notifications</h1><button className="btn ghost" onClick={() => mark()}>Mark all read</button></div>
    <div className="card">{n.length === 0 ? <p className="mute">You're all caught up.</p> : n.map(x => <div key={x.id} className={'item' + (x.read ? '' : ' unread')} onClick={() => !x.read && mark(x.id)} style={{ cursor: 'pointer' }}>
      <div className="grow">{x.text}<div className="mute">{ago(x.created)}</div></div>{!x.read && <span className="pill pending">New</span>}</div>)}</div></>
}

function Rewards() {
  const [a, , err] = useLoad('/achievements')
  if (!a) return <Loading err={err} />
  return <><h1>Rewards</h1><div className="grid g4"><div className="card stat"><span>XP</span><b>{a.xp}</b></div><div className="card stat"><span>Level</span><b>{a.level}</b></div><div className="card stat"><span>Weekly streak</span><b>🔥 {a.streak}</b></div></div>
    <div className="grid g4">{a.badges.map(b => <div key={b.code} className={'card badge' + (b.earned ? '' : ' off')}><div className="e">{b.icon}</div><b>{b.name}</b><div className="mute">{b.earned ? 'Earned ' + b.at : 'Not yet'}</div></div>)}</div></>
}

function Weekly() {
  const [w, , err] = useLoad('/weekly')
  if (!w) return <Loading err={err} />
  return <><h1>Weekly summary</h1><p className="mute">Week ending {w.week_ending}{w.is_weekend ? ' · Weekend report 🎉' : ''}. Generated when you open this page.</p>
    {w.groups.length === 0 ? <Empty text="Join a group to see a weekly summary." /> : w.groups.map(g => <div className="card" key={g.group}><h2>{g.group}</h2>
      <div className="grid g4"><div className="stat"><span>Expenses added</span><b>{inr(g.expenses_added)}</b></div><div className="stat"><span>Payments confirmed</span><b>{inr(g.payments_confirmed)}</b></div><div className="stat"><span>Outstanding</span><b>{inr(g.outstanding)}</b></div></div>
      <div style={{ height: 130 }}><ResponsiveContainer><BarChart data={[{ n: 'Added', v: g.expenses_added }, { n: 'Confirmed', v: g.payments_confirmed }, { n: 'Outstanding', v: g.outstanding }]}><XAxis dataKey="n" /><YAxis hide /><Tooltip formatter={v => inr(v)} /><Bar dataKey="v" fill="#7041C6" radius={8} /></BarChart></ResponsiveContainer></div>
      <Bar_ pct={g.progress.pct} ok={g.progress.pct === 100} /><p className="mute">Group progress {g.progress.pct}%</p>
      <div className="row wrap"><div className="grow"><b>Still to settle</b>{g.pending.length ? g.pending.map(u => <div key={u.id} className="mute">{u.avatar} {u.name} · {inr(u.owe)}</div>) : <div className="mute">Everyone's clear</div>}</div>
        <div className="grow"><b>Debt free 💸</b>{g.cleared.length ? g.cleared.map(u => <div key={u.id} className="mute">{u.avatar} {u.name}</div>) : <div className="mute">Not yet</div>}</div></div></div>)}</>
}

function Hist() {
  const [st, setSt] = useState('all'), [h, , err] = useLoad('/history?status=' + st, [st]), [open, setOpen] = useState(null)
  return <><h1>Transaction history</h1><div className="filters tabs">{[['all', 'All'], ['pending', 'Pending'], ['awaiting', 'Awaiting Confirmation'], ['approved', 'Approved'], ['rejected', 'Rejected']].map(([k, l]) => <button key={k} className={st === k ? 'on' : ''} onClick={() => setSt(k)}>{l}</button>)}</div>
    {!h ? <Loading err={err} /> : h.length === 0 ? <Empty text="Nothing matches this filter." /> : <div className="card">{h.map((x, i) => <div className="item" key={i} onClick={() => setOpen(open === i ? null : i)} style={{ cursor: 'pointer', flexWrap: 'wrap' }}>
      <div className="grow"><b>{x.title}</b><div className="mute">{x.group} · {x.date} · {x.kind === 'payment' ? 'payment' : `${x.payer} paid, ${x.debtor}'s share`}</div></div><b>{inr(x.amount)}</b><span className={'pill ' + x.status}>{x.status}</span>
      {open === i && <div className="mute" style={{ width: '100%' }}>Settled {inr(x.settled)} of {inr(x.amount)}{x.method && ' · via ' + x.method}{x.decided && ' · decided ' + ago(x.decided)}{x.reason && ' · rejected: ' + x.reason}</div>}</div>)}</div>}</>
}

function Profile() {
  const { user, setUser, say, logout } = useApp(), [name, setName] = useState(user.name), [avatar, setAvatar] = useState(user.avatar)
  const save = async () => { try { setUser(await api('/me', { method: 'PUT', body: { name, avatar } })); say('Profile saved.') } catch (e) { say(e.message, true) } }
  return <div className="card"><h1>Profile</h1><p className="mute">User ID {user.id} · {user.email}</p><label>Name</label><input value={name} onChange={e => setName(e.target.value)} />
    <label>Avatar</label><div className="demo" style={{ marginBottom: 12 }}>{['🦊', '🐼', '🐯', '🦄', '🦁', '🐙', '🦉'].map(a => <button key={a} style={avatar === a ? { borderColor: '#7041C6' } : {}} onClick={() => setAvatar(a)}>{a}</button>)}</div>
    <div className="row"><button className="btn" onClick={save}>Save changes</button><button className="btn ghost" onClick={logout}><LogOut size={16} />Log out</button></div></div>
}
