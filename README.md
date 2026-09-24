# Fit Vehicle Parts 产品数据中心

供应商零件资料的可追溯归档与归一化。

Django 5.2 + PostgreSQL。系统把供应商的 Excel / PDF / CSV 资料导入为可查询、可核对、可导出的产品档案。每个字段都能回看原始出处（文件、工作表或页、行、列）；拿不准的对应关系交给人工确认。

文档（可直接发布到 GitHub Pages，入口 `docs/index.html`）：

- [交付说明](docs/brief.html)：按交付要求逐条对应，先看这页
- [PRD](docs/prd.html)：需求、验收标准、样本数据风险清单
- [设计文档](docs/design.html)：架构、数据模型、流程、匹配规则，以及各项取舍
- [分步实施](docs/implementation.html)：搭建、演示、部署、维护手册
- [结果样例](docs/samples/)：主数据、供应商报价、待确认清单、导入报告、查询结果导出示例

## 本机运行

依赖 Python 3.12+、[uv](https://docs.astral.sh/uv/)，以及一个 PostgreSQL 16 实例（可以共用已有实例）。

```bash
# 在已有 PG 中建独立角色与库（幂等）
docker exec -i <pg容器> psql -U <超级用户> -d postgres -v pw=<密码> < scripts/create_db.sql

uv sync
cp .env.example .env          # 修改 DATABASE_URL
uv run python manage.py migrate
uv run python manage.py demo --with-increment --noinput --reviewer reviewer --password <密码>
uv run python manage.py runserver    # http://127.0.0.1:8000/ ，用上面的复核账号登录
uv run pytest
```

## 服务器（Docker Compose）

```bash
cp .env.example .env          # 设置 DJANGO_SECRET_KEY、POSTGRES_PASSWORD、DJANGO_ALLOWED_HOSTS 等
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `import_source <文件> [--supplier X] [--mapping m.yaml] [--partial] [--dry-run]` | 导入资料：原件归档，识别新增 / 更新 / 冲突 / 本次未出现，随后自动匹配 |
| `run_matching [--dry-run]` | 按当前规则重新评估候选并聚类；证据没变时保留人工决定 |
| `export_data [--out 目录] [--only master offers review report]` | 全量导出 xlsx |
| `export_data --query <编号/关键词> [--supplier X] [--state 分类]` | 导出这次查询的整理结果（与网页查询页“导出查询结果”一致） |
| `import_review <review_list.xlsx> --reviewer 姓名` | 回写离线填写的复核决定，全部通过才提交 |
| `make_demo_samples` | 生成增量演示文件（`samples/demo/`） |
| `demo [--with-increment] [--noinput]` | 清空业务数据后跑完整演示流程（脚本 / Docker 中必须加 `--noinput`） |
| `reset_data [--noinput]` | 只清空业务数据（保留用户账号），之后可手动导入 |

可调规则都在 `config/rules/`：列别名 `column_aliases.yaml`、类别同义词 `synonyms.yaml`、匹配参数 `matching.yaml`（带版本号）。
