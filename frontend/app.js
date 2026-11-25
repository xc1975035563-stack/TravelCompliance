const state = { token: sessionStorage.getItem("token") || "", user: null, page: "home", error: "" };

const labels = { onsite: "项目驻场", client_visit: "客户拜访", training: "培训开会" };
const roles = { traveler: "出行人", booker: "代订人", dept_approver: "部门审批人", finance: "财务审核人", admin: "差旅管理员" };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { Authorization: "Bearer " + state.token } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.message || "请求失败");
    error.code = data.error_code;
    throw error;
  }
  return data;
}

function h(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  });
  (Array.isArray(children) ? children : [children]).forEach((child) => {
    if (child === null || child === undefined || child === false) return;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  });
  return node;
}

function tag(text, kind) {
  return h("span", { class: "tag " + (kind || "") }, text);
}

function go(page) {
  state.page = page;
  state.error = "";
  render();
}

async function login(username) {
  const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password: "DemoOnly#2026" }) });
  state.token = data.token;
  state.user = data.user;
  sessionStorage.setItem("token", data.token);
  state.page = "home";
  render();
}

function shell(content) {
  const items = [
    ["home", "总览"],
    ["policies", "制度版本"],
    ["applications", "出差申请"],
    ["approvals", "审批待办"],
    ["orders", "订单与补单"],
    ["reimbursements", "报销核定"],
    ["failures", "失败原因"],
  ];
  if (["finance", "admin", "dept_approver"].includes(state.user.role)) {
    items.splice(6, 0, ["reconcile", "对账归集"]);
  }
  if (state.user.role === "admin") {
    items.push(["audit", "审计"], ["evaluations", "核对用例"], ["metrics", "监控"]);
  }
  return h("div", { class: "shell" }, [
    h("nav", {}, [
      h("div", { class: "eyebrow" }, "TravelCompliance"),
      h("strong", {}, "差旅合规"),
      ...items.map(([id, label]) => h("button", { class: state.page === id ? "active" : "", onClick: () => go(id) }, label)),
    ]),
    h("main", {}, [
      h("div", { class: "top" }, [
        h("div", {}, [h("div", { class: "muted" }, "演示环境，口令与密钥都不能用于生产"), h("strong", {}, `${state.user.display_name} · ${roles[state.user.role] || state.user.role}`)]),
        h("button", { class: "ghost", onClick: () => { sessionStorage.removeItem("token"); state.token = ""; state.user = null; render(); } }, "退出"),
      ]),
      state.error ? h("p", { class: "error" }, state.error) : null,
      content,
    ]),
  ]);
}

function loginView() {
  const accounts = [
    ["liwei", "李维 · 出行人 · 专员"],
    ["zhoumin", "周敏 · 出行人 · 经理"],
    ["booker", "何安 · 代订人"],
    ["dept-rd", "陈衡 · 研发审批"],
    ["dept-mkt", "苏晚 · 市场审批"],
    ["finance", "梁清 · 财务"],
    ["admin", "沈知 · 管理员"],
  ];
  return h("div", { class: "login" }, [
    h("section", { class: "login-card" }, [
      h("div", { class: "eyebrow" }, "TravelCompliance"),
      h("h1", {}, "差旅合规与单据核对"),
      h("p", { class: "muted" }, "覆盖项目驻场、客户拜访、培训开会。金额只由制度版本上的规则计算。"),
      h("div", { class: "banner" }, "演示口令统一为 DemoOnly#2026。JWT 默认密钥 DEMO_ONLY_JWT_SECRET_NOT_FOR_PRODUCTION，仅用于本地演示。"),
      h("div", { class: "accounts" }, accounts.map(([username, label]) => h("button", { onClick: () => login(username).catch((error) => alert(error.message)) }, label))),
    ]),
  ]);
}

async function homeView() {
  const [apps, claims] = await Promise.all([api("/api/applications"), api("/api/reimbursements")]);
  const findApp = (code) => apps.find((item) => item.code === code);
  const findClaim = (code) => {
    const app = findApp(code);
    return claims.find((item) => app && item.application_id === app.id && item.status === "approved")
      || claims.find((item) => app && item.application_id === app.id);
  };
  const cards = [
    ["制度变更不改写历史", "SQ-VISIT-FREEZE", "v1 核定 1020.00。发布更高住宿限额后，这张单仍是 1020.00。"],
    ["过夜无票与当天往返", "SQ-VISIT-DAY", "当天往返无住宿票仍可报 460.00。过夜无票会连带拒交通和补助，见核对用例。"],
    ["培训只发首尾两天", "SQ-TRAIN-001", "可报 800.00，其中补助 240.00。会期酒店订单已被拒绝。"],
    ["驻场日期重叠", "SQ-ONSITE-OVL", "同一人、同一项目的第二张驻场申请状态为拒绝。"],
    ["两笔报销抢一张票", "SQ-VISIT-DAY", "后一笔报销单的拒绝码是 INVOICE_ALREADY_CLAIMED。管理员可再跑并发脚本。"],
  ];
  return h("div", {}, [
    h("h2", {}, "五件可以当场演示的事"),
    h("div", { class: "grid" }, cards.map(([title, code, text]) => {
      const claim = findClaim(code);
      const app = findApp(code);
      return h("article", { class: "card" }, [
        h("h3", {}, title),
        h("p", { class: "muted" }, text),
        h("div", { class: "row" }, [
          app ? tag(labels[app.trip_type] || app.trip_type) : null,
          app ? tag(app.status, app.status === "rejected" ? "bad" : "ok") : null,
          claim ? h("button", { class: "primary", onClick: () => openClaim(claim.id) }, "打开核定") : null,
          app ? h("button", { class: "ghost", onClick: () => openApp(app.id) }, app.code) : null,
        ]),
      ]);
    })),
  ]);
}

async function policiesView() {
  const rows = await api("/api/policies");
  const box = h("div", {}, [h("h2", {}, "制度与版本")]);
  box.append(h("div", { class: "grid" }, await Promise.all(rows.map(async (item) => {
    const detail = await api("/api/policies/" + item.id);
    return h("article", { class: "card" }, [
      h("div", { class: "row" }, [h("h3", {}, item.code), tag(item.status, item.status === "active" ? "ok" : "")]),
      h("p", {}, item.note || "无说明"),
      h("p", { class: "muted" }, `周末驻场补助：${item.onsite_weekend_allowance ? "发放" : "不发放"}；拆段间隔 ${item.onsite_split_gap_days} 天；超标策略 ${item.over_standard_action}`),
      h("p", {}, `条款 ${detail.clauses.length} 条，差标 ${detail.standards.length} 条`),
    ]);
  }))));
  if (state.user.role === "admin") {
    const amount = h("input", { value: "600" });
    box.append(h("article", { class: "card", style: "margin-top:12px" }, [
      h("h3", {}, "发布新制度版本"),
      h("p", { class: "muted" }, "只新增版本。已保存的报销结果继续指向旧版本。"),
      h("div", { class: "row" }, [
        h("label", {}, ["专员 / 二线城市 / 住宿每晚限额", amount]),
        h("button", { class: "primary", onClick: async () => {
          await api("/api/policies", { method: "POST", body: JSON.stringify({ note: "演示：上调专员二线住宿限额", standards: [{ rank: "staff", city_tier: "T2", item: "hotel", amount: amount.value }] }) });
          go("policies");
        } }, "发布并生效"),
      ]),
    ]));
  }
  return box;
}

async function applicationsView() {
  const [rows, meta] = await Promise.all([api("/api/applications"), api("/api/meta")]);
  const trip = h("select", {}, [h("option", { value: "" }, "全部类型"), ...meta.trip_types.map((item) => h("option", { value: item.id }, item.label))]);
  const tableHost = h("div");
  const draw = () => {
    tableHost.replaceChildren(renderTable(rows.filter((item) => !trip.value || item.trip_type === trip.value), [
      ["单号", (item) => item.code],
      ["类型", (item) => labels[item.trip_type]],
      ["场景", (item) => item.scenario],
      ["行程", (item) => `${item.origin} → ${item.destination} ${item.start_date} 至 ${item.end_date}`],
      ["状态", (item) => item.status + (item.rejection_code ? " / " + item.rejection_code : "")],
    ], (item) => openApp(item.id)));
  };
  trip.addEventListener("change", draw);
  draw();
  const form = state.user.role === "traveler" || state.user.role === "admin" ? applicationForm(meta) : null;
  return h("div", {}, [h("div", { class: "top" }, [h("h2", {}, "出差申请"), trip]), tableHost, form]);
}

function applicationForm(meta) {
  const trip = h("select", {}, meta.trip_types.map((item) => h("option", { value: item.id }, item.label)));
  const fields = h("div", { class: "grid" });
  const reason = input("事由");
  const origin = input("出发地", "杭州");
  const destination = input("目的地", "上海");
  const start = input("开始", "2026-09-01", "date");
  const end = input("结束", "2026-09-07", "date");
  const budget = input("预算", "8000");
  const owner = h("select", {}, [h("option", { value: "project" }, "项目 / 成本中心"), h("option", { value: "department" }, "部门")]);
  const project = h("select", {}, meta.projects.map((item) => h("option", { value: item.id }, item.name)));
  const department = h("select", {}, meta.departments.map((item) => h("option", { value: item.id }, item.name)));
  const client = input("拜访对象", "南湖精密");
  const purpose = input("拜访事由", "方案确认");
  const event = h("select", {}, meta.events.map((item) => h("option", { value: item.id }, item.name)));
  const lodging = h("input", { type: "checkbox" });
  const meals = h("input", { type: "checkbox" });
  const redraw = () => {
    const type = trip.value;
    const nodes = [
      reason, origin, destination, start, end, budget, h("label", {}, ["费用归属", owner]),
      owner.value === "project" ? h("label", {}, ["项目", project]) : h("label", {}, ["部门", department]),
      type === "client_visit" ? client : null,
      type === "client_visit" ? purpose : null,
      type === "training" ? h("label", {}, ["会议 / 培训", event]) : null,
      type === "onsite" ? h("label", {}, ["项目部提供住宿", lodging]) : null,
      type === "onsite" ? h("label", {}, ["项目部提供用餐", meals]) : null,
    ].filter(Boolean);
    fields.replaceChildren(...nodes);
  };
  trip.addEventListener("change", redraw);
  owner.addEventListener("change", redraw);
  redraw();
  return h("article", { class: "card", style: "margin-top:12px" }, [
    h("h3", {}, "新建申请"),
    trip,
    fields,
    h("button", { class: "primary", onClick: async () => {
      try {
        const created = await api("/api/applications", { method: "POST", body: JSON.stringify({
          trip_type: trip.value,
          traveler_ids: [state.user.role === "traveler" ? state.user.id : "u-liwei"],
          reason: reason.querySelector("input").value,
          origin: origin.querySelector("input").value,
          destination: destination.querySelector("input").value,
          start_date: start.querySelector("input").value,
          end_date: end.querySelector("input").value,
          estimated_budget: budget.querySelector("input").value,
          cost_owner_type: owner.value,
          project_id: owner.value === "project" ? project.value : null,
          department_id: department.value,
          client_name: trip.value === "client_visit" ? client.querySelector("input").value : null,
          visit_purpose: trip.value === "client_visit" ? purpose.querySelector("input").value : null,
          event_id: trip.value === "training" ? event.value : null,
          project_provides_lodging: lodging.checked,
          project_provides_meals: meals.checked,
        }) });
        await api(`/api/applications/${created.id}/submit`, { method: "POST" });
        openApp(created.id);
      } catch (error) {
        state.error = `${error.code || ""} ${error.message}`;
        render();
      }
    } }, "保存并提交"),
  ]);
}

function input(label, value = "", type = "text") {
  return h("label", {}, [label, h("input", { value, type })]);
}

async function openApp(id) {
  state.page = "application";
  state.currentId = id;
  render();
}

async function applicationDetail() {
  const data = await api("/api/applications/" + state.currentId);
  const app = data.application;
  return h("div", {}, [
    h("button", { class: "ghost", onClick: () => go("applications") }, "返回列表"),
    h("h2", {}, `${app.code} · ${labels[app.trip_type]}`),
    h("p", {}, app.scenario || app.reason),
    h("div", { class: "row" }, [tag(app.status, app.status === "rejected" ? "bad" : "ok"), tag(app.cost_center_code || "无成本中心")]),
    app.rejection_reason ? h("p", { class: "error" }, `${app.rejection_code}：${app.rejection_reason}`) : null,
    h("p", {}, `${app.origin} → ${app.destination}，${app.start_date} 至 ${app.end_date}，预算 ${app.estimated_budget}`),
    app.client_name ? h("p", {}, `拜访对象：${app.client_name}。${app.visit_purpose || ""}`) : null,
    h("h3", {}, "订单"),
    renderTable(data.orders, [["类型", (item) => item.type + (item.is_supplement ? " / 补单" : "")], ["金额", (item) => item.amount], ["状态", (item) => item.compliance_status], ["说明", (item) => item.compliance_message || ""]]),
    h("h3", {}, "报销"),
    renderTable(data.reimbursements, [["单号", (item) => item.code], ["状态", (item) => item.status], ["可报", (item) => item.personal_claimable], ["制度", (item) => item.result && item.result.policy_version]], (item) => openClaim(item.id)),
  ]);
}

async function approvalsView() {
  let rows = [];
  try {
    rows = await api("/api/approvals");
  } catch (error) {
    return h("p", { class: "error" }, `${error.code} ${error.message}`);
  }
  return h("div", {}, [
    h("h2", {}, "审批待办"),
    renderTable(rows, [["对象", (item) => item.entity_type], ["步骤", (item) => item.step], ["部门", (item) => item.department_id || ""]], (item) => decide(item)),
  ]);
}

async function decide(task) {
  const comment = prompt("审批意见", "同意") || "";
  const approvedAmount = task.step === "over_finance" ? prompt("批准口径金额，不能留空", "2000") : null;
  try {
    await api(`/api/approvals/${task.id}/decision`, { method: "POST", body: JSON.stringify({ approve: true, comment, approved_amount: approvedAmount }) });
    go("approvals");
  } catch (error) {
    state.error = `${error.code} ${error.message}`;
    render();
  }
}

async function ordersView() {
  const rows = await api("/api/orders");
  let importBox = null;
  if (["admin", "booker", "finance"].includes(state.user.role)) {
    const text = h("textarea");
    const sample = await api("/api/samples/order-import");
    text.value = JSON.stringify(sample, null, 2);
    importBox = h("article", { class: "card", style: "margin-top:12px" }, [
      h("h3", {}, "导入订单"),
      h("p", { class: "muted" }, "导入的是虚构订单，不会连接航司、酒店或网约车。重复行按去重键剔除，缺字段的行不进入校验。"),
      text,
      h("button", { class: "primary", onClick: async () => {
        const result = await api("/api/orders/import", { method: "POST", body: JSON.stringify({ rows: JSON.parse(text.value) }) });
        alert(`去重 ${result.duplicate_count} 条，脏数据 ${result.dirty_count} 条，校验拒绝 ${result.rejected_count} 条。${result.notice}`);
        go("orders");
      } }, "导入"),
    ]);
  }
  return h("div", {}, [h("h2", {}, "订单与补单"), renderTable(rows, [
    ["类型", (item) => item.type],
    ["金额", (item) => item.amount],
    ["支付", (item) => item.payment_mode === "company" ? "公司统付" : "个人垫付"],
    ["补单", (item) => item.is_supplement ? "是" : "否"],
    ["差标", (item) => item.compliance_status],
    ["说明", (item) => item.compliance_message || item.compliance_code || ""],
  ]), importBox]);
}

async function reimbursementsView() {
  const rows = await api("/api/reimbursements");
  return h("div", {}, [h("h2", {}, "报销核定"), renderTable(rows, [
    ["单号", (item) => item.code],
    ["状态", (item) => item.status],
    ["类型", (item) => labels[(item.result || {}).trip_type] || ""],
    ["制度", (item) => (item.result || {}).policy_version || ""],
    ["可报", (item) => item.personal_claimable],
    ["自理", (item) => item.self_pay],
    ["补助", (item) => item.allowance],
    ["统付", (item) => item.company_paid],
  ], (item) => openClaim(item.id))]);
}

async function openClaim(id) {
  state.page = "claim";
  state.currentId = id;
  render();
}

async function claimDetail() {
  const row = await api("/api/reimbursements/" + state.currentId);
  const result = row.result || {};
  const explain = h("pre", { class: "muted" });
  return h("div", {}, [
    h("button", { class: "ghost", onClick: () => go("reimbursements") }, "返回列表"),
    h("h2", {}, `${row.code} · ${result.trip_type_label || ""}`),
    h("div", { class: "row" }, [
      tag(row.status, row.status === "rejected" ? "bad" : "ok"),
      tag("制度 " + (result.policy_version || "")),
      result.hypothetical ? tag("试算", "warn") : tag("已保存快照", "ok"),
    ]),
    h("div", { class: "grid" }, [
      metric("个人可报", row.personal_claimable),
      metric("个人自理", row.self_pay),
      metric("补助", row.allowance),
      metric("公司统付", row.company_paid),
    ]),
    result.refusal_reason ? h("p", { class: "error" }, `${result.refusal_code}：${result.refusal_reason}`) : null,
    (result.warnings || []).map((item) => h("p", { class: "banner" }, item)),
    result.weekend_rule_note ? h("p", {}, result.weekend_rule_note) : null,
    h("p", { class: "muted" }, result.simulated_verification_notice || ""),
    h("h3", {}, "费用明细"),
    renderTable(result.lines || [], [
      ["项目", (item) => item.title],
      ["可报", (item) => item.claimable],
      ["自理", (item) => item.self_pay],
      ["拒报", (item) => item.rejected],
      ["原因", (item) => item.reason || ""],
    ]),
    h("h3", {}, "未发放的日期"),
    renderTable(result.allowance_excluded_days || [], [["日期", (item) => item.date], ["原因", (item) => item.reason], ["条款", (item) => item.clause]]),
    h("h3", {}, "引用条款"),
    h("div", {}, (result.clauses || []).map((item) => h("div", { class: "clause" }, [h("strong", {}, item.code + " "), item.text]))),
    h("button", { class: "primary", onClick: async () => {
      const data = await api(`/api/reimbursements/${row.id}/explain`, { method: "POST", body: JSON.stringify({}) });
      explain.textContent = data.text;
    } }, "生成说明（不重新计算金额）"),
    explain,
  ]);
}

function metric(label, value) {
  return h("article", { class: "card" }, [h("div", { class: "muted" }, label), h("div", { class: "money" }, value ?? "—")]);
}

async function reconcileView() {
  const group = h("select", {}, [["trip_type", "出差类型"], ["department", "部门"], ["project", "项目"]].map(([value, label]) => h("option", { value }, label)));
  const host = h("div");
  const load = async () => {
    try {
      const data = await api("/api/reconcile?group_by=" + group.value);
      host.replaceChildren(h("p", {}, "合计 " + data.total), renderTable(data.buckets, [["归集", (item) => item.label || item.key], ["金额", (item) => item.amount], ["报销", (item) => item.personal], ["统付", (item) => item.company]]));
    } catch (error) {
      host.replaceChildren(h("p", { class: "error" }, `${error.code}：${error.message}`));
    }
  };
  group.addEventListener("change", load);
  await load();
  return h("div", {}, [h("div", { class: "top" }, [h("h2", {}, "对账与费用归集"), group]), h("p", { class: "muted" }, "只汇总已审批通过的报销。统付不重复付给个人。归属对不上时接口直接报错，不返回近似金额。"), host]);
}

async function failuresView() {
  const data = await api("/api/failures");
  return h("div", {}, [
    h("h2", {}, "失败原因"),
    h("h3", {}, "申请"),
    renderTable(data.applications, [["单号", (item) => item.code], ["类型", (item) => labels[item.trip_type] || ""], ["场景", (item) => item.scenario], ["代码", (item) => item.code_name], ["原因", (item) => item.reason]]),
    h("h3", {}, "订单"),
    renderTable(data.orders, [["类型", (item) => item.type], ["代码", (item) => item.code], ["原因", (item) => item.reason]]),
    h("h3", {}, "报销"),
    renderTable(data.reimbursements, [["单号", (item) => item.code], ["类型", (item) => labels[item.trip_type] || ""], ["代码", (item) => item.refusal_code], ["原因", (item) => item.refusal_reason]], (item) => openClaim(item.id)),
  ]);
}

async function auditView() {
  const rows = await api("/api/audit");
  return h("div", {}, [h("h2", {}, "审计"), renderTable(rows, [
    ["时间", (item) => item.at],
    ["人", (item) => item.actor_name],
    ["动作", (item) => item.action],
    ["对象", (item) => item.entity_type],
    ["制度", (item) => item.policy_version || ""],
    ["结果", (item) => item.success ? "成功" : "失败"],
    ["结论", (item) => item.conclusion_summary],
  ])]);
}

async function evaluationsView() {
  const host = h("div");
  const button = h("button", { class: "primary", onClick: async () => {
    host.replaceChildren("正在运行，不调用对话模型……");
    const report = await api("/api/admin/evaluations/run", { method: "POST" });
    host.replaceChildren(
      h("p", {}, `通过 ${report.passed} / ${report.total}`),
      renderTable(report.cases, [["用例", (item) => item.id], ["类型", (item) => labels[item.trip_type] || item.trip_type], ["结果", (item) => item.passed ? "通过" : "失败"], ["说明", (item) => item.title], ["差异", (item) => (item.errors || []).join("；")]]),
    );
  } }, "运行全部核对用例");
  return h("div", {}, [h("h2", {}, "核对用例"), h("p", { class: "muted" }, "用例直接调用规则引擎，不用大模型当裁判。"), button, host]);
}

async function metricsView() {
  const data = await api("/api/admin/metrics");
  const raceHost = h("div");
  return h("div", {}, [
    h("h2", {}, "监控"),
    h("p", { class: "banner" }, "这些计数在进程内存里。服务重启后清零，不会写成历史业绩。"),
    h("div", { class: "grid" }, [
      metric("申请提交", data.application_submitted),
      metric("核定次数", data.reimbursement_count),
      metric("平均耗时 ms", data.reimbursement_avg_ms),
      metric("规则拒绝", data.rule_reject_count),
      metric("三单不匹配", data.three_way_mismatch_count),
      metric("模型越权", data.model_failure_count),
    ]),
    h("p", {}, "按类型核定：" + JSON.stringify(data.reimbursement_by_type)),
    h("button", { class: "primary", onClick: async () => {
      const race = await api("/api/admin/invoice-race", { method: "POST" });
      raceHost.replaceChildren(h("p", {}, `并发结果 ${race.results.join(" / ")}，通过：${race.passed}`));
    } }, "跑两线程抢同一张发票"),
    raceHost,
  ]);
}

function renderTable(rows, columns, onClick) {
  return h("table", {}, [
    h("thead", {}, [h("tr", {}, columns.map(([title]) => h("th", {}, title)))]),
    h("tbody", {}, rows.map((row) => h("tr", onClick ? { onClick: () => onClick(row), style: "cursor:pointer" } : {}, columns.map(([, reader]) => h("td", {}, reader(row)))))),
  ]);
}

const pages = {
  home: homeView,
  policies: policiesView,
  applications: applicationsView,
  application: applicationDetail,
  approvals: approvalsView,
  orders: ordersView,
  reimbursements: reimbursementsView,
  claim: claimDetail,
  reconcile: reconcileView,
  failures: failuresView,
  audit: auditView,
  evaluations: evaluationsView,
  metrics: metricsView,
};

async function render() {
  const root = document.getElementById("app");
  if (!state.token) {
    root.replaceChildren(loginView());
    return;
  }
  if (!state.user) {
    try {
      state.user = await api("/api/me");
    } catch (error) {
      state.token = "";
      root.replaceChildren(loginView());
      return;
    }
  }
  root.replaceChildren(shell(h("p", {}, "加载中…")));
  try {
    const view = await pages[state.page]();
    root.replaceChildren(shell(view));
  } catch (error) {
    state.error = `${error.code || ""} ${error.message}`;
    root.replaceChildren(shell(h("p", { class: "error" }, state.error)));
  }
}

render();
