// 时段外事实的唯一展示组件：任何页面要露窗态都只能用它，禁止各写各的文案/颜色
export function OowBadge({ value }) {
  return value
    ? <span className="badge badge-oow">时段外</span>
    : <span className="badge badge-in">时段内</span>
}

export function WindowBadge({ status }) {
  return status === '窗开'
    ? <span className="badge badge-in">窗开</span>
    : <span className="badge badge-closed">窗关</span>
}
