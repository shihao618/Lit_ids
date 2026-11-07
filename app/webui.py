"""统一响应式页面基座。

所有前台/后台页面共用同一份 HTML 和同一套布局规则，不维护独立的 PC/移动端模板。
具体页面只补充业务样式；这里只负责：
  * 页面外壳宽度（侧栏/主内容）
  * 表单行、按钮、输入框的自动换行与收缩
  * 表格/图片/代码块在窄屏不撑破容器
"""

RESPONSIVE_CSS = """
/* ---------- 统一响应式基座：页面只有一套结构，不区分 PC/移动端 ---------- */
:root{
  --sidebar-w:clamp(200px,16vw,232px);
  --page-gap:clamp(12px,1.6vw,24px);
}
img{max-width:100%;height:auto}
input,select,textarea{max-width:100%;box-sizing:border-box}
.row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;min-width:0}
.row > input,
.row > select{flex:1 1 150px;width:auto!important;min-width:0;max-width:100%}
.row > button,
.row > .btn,
.row > label{flex:0 1 auto;max-width:100%}
.row > span{flex:0 0 auto}
.row > .note{flex:1 1 100%}
.table-wrap{max-width:100%;overflow-x:auto}
.doc-sidebar{width:var(--sidebar-w)}
.docs-main{margin-left:var(--sidebar-w)}
.admin-sidebar{width:var(--sidebar-w)}
.admin-main{margin-left:var(--sidebar-w)}
.docs-content{padding-left:var(--page-gap);padding-right:var(--page-gap)}
.admin-content{padding-left:var(--page-gap);padding-right:var(--page-gap)}
@media(max-width:900px){
  .docs-main,.admin-main{margin-left:0}
  .doc-sidebar,.admin-sidebar{width:min(300px,85vw)}
}
"""
