"""业务路由汇总：固定路径的复核路由必须早于通用资源路由注册。"""
from __future__ import annotations

from app.routers import resource, review

ROUTERS = [review, resource]
