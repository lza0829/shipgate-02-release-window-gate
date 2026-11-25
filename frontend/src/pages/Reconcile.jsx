import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { OowBadge } from '../components/Badge'

export default function Reconcile() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/对账').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const s = data.发版统计 || {}
  return (
    <div className="panel">
      <h2>发布对账</h2>
      <div className="grid">
        {Object.entries(s).map(([k, v]) => (
          <div className={`stat${k === '时段外放行' && v ? ' stat-oow' : ''}`} key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
        <div className="stat"><div className="n">{data.晋升事件数}</div><div className="l">晋升事件数</div></div>
        <div className="stat"><div className="n">{data.晋升时段内}</div><div className="l">晋升·时段内</div></div>
        <div className="stat stat-oow"><div className="n">{data.晋升时段外}</div><div className="l">晋升·时段外</div></div>
      </div>
      <h2>名册与实例</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>名册版本</th><th>名册窗态</th><th>实例版本们</th><th>实例窗态</th><th>红灯数</th></tr></thead>
          <tbody>
            {(data.名册与实例 || []).map((r, i) => (
              <tr key={i}>
                <td>{r.应用}</td><td>{r.环境}</td><td>{r.名册版本}</td>
                <td><OowBadge value={r.名册时段外} /></td>
                <td>{r.实例版本们 || '-'}</td>
                <td>{r.实例时段外们 ? String(r.实例时段外们).split(',').map((f, j) => (
                  <span key={j} style={{ marginRight: 4 }}><OowBadge value={f === '1'} /></span>
                )) : '-'}</td>
                <td>{r.红灯数}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
