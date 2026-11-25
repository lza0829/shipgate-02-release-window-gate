import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { OowBadge, WindowBadge } from '../components/Badge'

export default function Promote() {
  const [rows, setRows] = useState([])
  const [rid, setRid] = useState('')
  const [env, setEnv] = useState('预')
  const [preview, setPreview] = useState(null)
  const [receipt, setReceipt] = useState(null)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    const all = await api('/发版单')
    const active = all.filter((r) => ['已入闸', '晋升中'].includes(r.状态))
    setRows(active)
    setRid((cur) => cur || (active[0] ? String(active[0].编号) : ''))
    setReceipt(null)
  }, [])

  useEffect(() => { load().catch((e) => setErr(e.message)) }, [load])

  // 读侧按写侧规则重放：选单或切环境即拉预判，窗态/时段外与写口同一函数产出
  const replay = useCallback(async () => {
    setPreview(null)
    setErr('')
    if (!rid) return
    try {
      setPreview(await api(`/发版单/${rid}/放行预判?目标环境=${encodeURIComponent(env)}`))
    } catch (e) { setErr(e.message) }
  }, [rid, env])

  useEffect(() => { replay() }, [replay])

  const go = async () => {
    setErr(''); setReceipt(null)
    try {
      const r = await api(`/发版单/${rid}/晋升`, {
        method: 'POST',
        body: JSON.stringify({ 目标环境: env }),
      })
      setReceipt(r)
      await load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>环境晋升 · 维护时段闸</h2>
      {err && <p className="err">{err}</p>}

      {preview && (
        <div className={`gate-box ${preview.时段外 ? 'gate-warn' : 'gate-ok'}`}>
          <div className="gate-title">
            放行预判 · 目标环境 {preview.目标环境} · <WindowBadge status={preview.窗口状态} />
            {' '}<OowBadge value={preview.时段外} />
          </div>
          <div>{preview.提示}</div>
        </div>
      )}

      {receipt && (
        <div className={`receipt ${receipt.时段外 ? 'receipt-oow' : ''}`}>
          <strong>放行回执：</strong>{receipt.回执}
          {' '}<WindowBadge status={receipt.窗口状态} /> <OowBadge value={receipt.时段外} />
        </div>
      )}

      <div className="row">
        <label>发版单
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {rows.map((r) => (
              <option key={r.编号} value={r.编号}>
                {r.发版单号} · {r.应用} · {r.当前环境}
                {r.时段外 ? ' · 时段外' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>目标环境
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="测">测</option>
            <option value="预">预</option>
            <option value="产">产</option>
          </select>
        </label>
        <button
          type="button"
          className={preview?.时段外 ? 'warn' : ''}
          onClick={go}
          disabled={!preview?.可放行}
        >
          {preview?.时段外 ? '时段外仍放行（留痕）' : '晋升'}
        </button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>当前环境</th><th>窗态</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.目标版本}</td>
                <td>{r.状态}</td><td>{r.当前环境}</td>
                <td><OowBadge value={r.时段外} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
