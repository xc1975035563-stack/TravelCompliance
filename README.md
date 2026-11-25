# TravelCompliance

差旅合规与单据核对助手。它处理企业里真实会卡住的一段流程：制度版本、出差申请、审批、出行订单、票据和报销核定要对得上，并且按出差类型使用不同规则。

这不是商旅预订平台，也不是通用报销聊天机器人。机票、火车、酒店、用车都用订单数据表示。发票查验是可注入的模拟结果。报销金额只由确定性规则引擎计算。

演示数据全部虚构，例如青禾工业项目组、南湖精密、江城培训中心。演示口令是 `DemoOnly#2026`。默认签名密钥是 `DEMO_ONLY_JWT_SECRET_NOT_FOR_PRODUCTION`。这两项都只给本地演示用。

## 本地启动

需要 Python 3.11 及以上。

```powershell
cd D:\PythonCode\Expedio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "backend"
cd backend
python -m app.seed --reset
python -m uvicorn app.main:app --port 8000
```

浏览器打开 http://127.0.0.1:8000 ，在登录页点一个演示身份。

本地默认数据库是 SQLite 文件 `backend/travel_demo.db`。制度、申请、订单、票据核销、报销结果和审计都写在这一个库里。要换成 PostgreSQL，先启动数据库，再设置：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://travel:demo-only-not-prod@127.0.0.1:5432/travel"
```

`demo-only-not-prod` 也只是演示口令。

重新导入虚构数据：

```powershell
cd backend
python -m app.seed --reset
```

只跑核对用例，不启动页面：

```powershell
$env:PYTHONPATH = "backend"
python -m pytest backend/tests -q
```

证明两笔报销抢同一张发票：

```powershell
$env:PYTHONPATH = "backend"
python scripts/concurrency_invoice_race.py
```

成功时两次运行都会打印 `INVOICE_ALREADY_CLAIMED` 和 `ok` 各一次。

页面上管理员的「监控」也可以触发同一次演示。监控数字是进程内计数，重启后清零，不能当成历史业务量。

## Docker 与回滚

```powershell
$env:APP_VERSION = "1.0.0"
docker compose build
docker compose up -d
```

镜像名是 `travel-compliance:1.0.0`。改完代码后打下一个标签，不要覆盖旧标签：

```powershell
$env:APP_VERSION = "1.1.0"
docker compose build
docker compose up -d
```

回到上一版：

```powershell
.\scripts\rollback.ps1 -Version 1.0.0
```

脚本会先确认本地存在该标签，再按这个标签启动。SQLite 数据在 Docker volume `tc-data` 里。代码回滚不会自动改表结构；演示环境要清空数据时，删掉 volume 后重新 `up`，启动时会再次写入虚构数据。

## 项目结构

```text
backend/app/engine.py        规则引擎，负责金额、拒报和条款
backend/app/evaluations.py   核对用例，不调用对话模型
backend/app/services.py      申请、审批、订单、核销、报销
backend/app/seed.py          虚构示例数据
frontend/                    登录后的核对页面
data/sample/order_import.json
Dockerfile
docker-compose.yml
```

主链路是：制度版本 → 申请 / 订单 / 票据 → 按出差类型计算 → 引用条款。没有多智能体编排。

## 三种出差类型

申请单上的出差类型必填。

- 项目驻场必须归到项目或成本中心。周末是否发伙食和市内交通补助由制度开关决定，核定结果会写出当前开关。项目部提供住宿或用餐时，对应费用和补助停发。同一人、同一项目日期重叠或间隔过短的后单拒绝。中途返回必须走行程变更，未补批不能报销。
- 客户拜访必须写拜访对象和事由。当天往返没有住宿发票仍然核定交通和当天补助。过夜但没有有效住宿发票时，住宿、城市间交通、伙食补助和市内交通补助一起不发。客户接待用餐或提供交通的当天，对应补助停发。没有项目时可以归部门，但必须在申请单上选明。
- 培训开会必须关联会议名称、举办方、日期和是否统一安排食宿。只在报到日和返程日发伙食、市内交通补助，会期不发。举办方安排住宿时，会期酒店订单直接拒绝，并且不会因此拒绝往返交通。

公司统付进入对账，不付给个人。个人垫付通过核定后才进入报销支付。

## 五条演示路径

1. 制度变更不改写历史。用管理员打开报销 `SQ-VISIT-FREEZE`，个人可报 `1020.00`，制度版本 `v1`。在「制度版本」把专员二线住宿限额改成 600 并发布。再打开这张报销，金额和版本仍是 `1020.00` / `v1`。用新版本做试算会得到 `1120.00`，试算不会保存。
2. 过夜无票和当天往返不是同一条规则。`SQ-VISIT-DAY` 没有住宿发票，个人可报 `460.00`，含交通 300 和当天补助 160。核对用例 `visit_overnight_no_hotel_cascade` 把过夜拜访缺住宿票判为整段不可报。
3. 培训开会。`SQ-TRAIN-001` 个人可报 `800.00`，补助 `240.00`，只覆盖报到日和返程日。同申请下的会期酒店订单状态是拒绝，代码 `CONFERENCE_HOTEL_REJECTED`。
4. 驻场重叠。`SQ-ONSITE-001` 已占用李维在青禾工业项目 2026-03-02 至 2026-03-10。`SQ-ONSITE-OVL` 的重叠申请状态是拒绝，代码 `ONSITE_OVERLAP`。`SQ-ONSITE-CHANGE` 的中途返回还没补批，对应报销代码是 `CHANGE_NOT_APPROVED`。
5. 两张报销抢一张票。`SQ-VISIT-DAY` 有一张通过的报销和一张被拒绝的后单，拒绝码 `INVOICE_ALREADY_CLAIMED`。再执行 `python scripts/concurrency_invoice_race.py`，两线程同时提交时也只有一笔成功。

## 已实现和未实现

已实现：

- 制度按版本保存。申请、订单校验、报销核定都记下所用版本。
- 出行人、代订人、部门审批、财务复审、差旅管理员。越权返回 `ROLE_FORBIDDEN`。财务节点在部门节点之后。
- 机票、火车、酒店、用车订单和补单。写入时按制度和申请单校验。超标可以拦截，或填写原因后走超标审批。
- 模拟查验结果：通过、查无此票、发票已用、抬头或税号不符、金额不符。结果里写明这是模拟查验。
- 报销输出可报、自理、补助、拒报原因、条款和版本。统付与个人垫付分开。
- 按部门、项目、出差类型归集。归属或统计区间对不上时返回错误，不给近似金额。
- 审计日志。管理员能查，出行人只能看自己的单据。
- 40 条以上核对用例，三种类型都有通过和失败。
- 进程内监控。预算占用、票据核销、驻场日期使用数据库条件更新或唯一约束。
- 订单、票据、申请和报销预留 `currency`，演示数据都是 `CNY`。

没有做：

- 没有对接航司、铁路、酒店、网约车，没有座位和房价库存，没有在线支付。订单是录入或 JSON 导入。
- 没有对接税务机关。查验结果是演示时注入的。
- 没有接入生成式模型。说明文字只转述规则引擎已经算出的结果和条款；调用方如果传入金额，接口返回 `MODEL_CANNOT_SET_AMOUNT`。
- 没有 7×24 客服、行程保障、国际多币种结算，也没有 OA / ERP。
- 监控不会落盘，重启清零。没有真实 CI 和灰度，只有镜像标签回滚。

## 可以写进简历的三句话

1. 做了差旅制度版本和确定性核定引擎，覆盖项目驻场、客户拜访、培训开会。报销金额、超标自理和拒报原因由规则计算并引用条款；历史核定绑定当时的制度版本，后续调标不改写已出结果。

2. 打通出差申请、部门审批与财务复审、订单和补单差标校验、模拟票据查验、报销核定。引擎区分过夜缺票连带拒报、当天往返照常核定交通和补助、培训会期不发补助且拒会期住宿、驻场重叠或未补批的中途返回，并把公司统付与个人垫付分开入账。

3. 用数据库条件更新保证同一张发票只能被一笔报销核销，驻场日期用唯一约束防重叠占用。配套不依赖大模型的核对用例、审计日志、进程内监控，以及可用镜像标签回滚的 Docker Compose 演示环境。
