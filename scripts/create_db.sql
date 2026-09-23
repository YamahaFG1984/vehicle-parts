-- 在已有 PostgreSQL 实例中为本项目创建独立角色与数据库（幂等，可重复执行）。
-- 用法：docker exec -i <pg容器> psql -U <超级用户> -d postgres -v pw=<密码> < scripts/create_db.sql
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE vehicle_parts LOGIN CREATEDB PASSWORD %L', :'pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vehicle_parts') \gexec

-- 已存在时同步密码与权限（CREATEDB 供 pytest 创建测试库）
ALTER ROLE vehicle_parts WITH LOGIN CREATEDB PASSWORD :'pw';

SELECT 'CREATE DATABASE vehicle_parts OWNER vehicle_parts ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'vehicle_parts') \gexec
