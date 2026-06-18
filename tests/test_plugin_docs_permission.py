from tests.test_plugin_docs_support import *


def test_filter_features_by_permission_normal_user() -> None:
    """验证普通用户只能看到普通用户权限的功能"""
    from src.lib.plugin_docs import (
        FeatureDoc,
        filter_features_by_permission,
    )

    features = (
        FeatureDoc(
            slug="public",
            title="公开功能",
            summary="所有人可见",
            aliases=(),
            trigger="#public",
            permission=Permission.NORMAL,
            demo_filename="public.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="admin",
            title="管理员功能",
            summary="仅管理员可见",
            aliases=(),
            trigger="#admin",
            permission=Permission.GROUP_ADMIN,
            demo_filename="admin.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="superuser",
            title="超级管理员功能",
            summary="仅超级管理员可见",
            aliases=(),
            trigger="#superuser",
            permission=Permission.SUPERUSER,
            demo_filename="superuser.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
    )

    # 普通用户只能看到普通权限的功能
    normal_visible = filter_features_by_permission(features, Permission.NORMAL)
    assert len(normal_visible) == 1
    assert normal_visible[0].slug == "public"


def test_filter_features_by_permission_admin_user() -> None:
    """验证管理员可以看到管理员及以下权限的功能"""
    from src.lib.plugin_docs import FeatureDoc, filter_features_by_permission

    features = (
        FeatureDoc(
            slug="public",
            title="公开功能",
            summary="所有人可见",
            aliases=(),
            trigger="#public",
            permission=Permission.NORMAL,
            demo_filename="public.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="admin",
            title="管理员功能",
            summary="仅管理员可见",
            aliases=(),
            trigger="#admin",
            permission=Permission.GROUP_ADMIN,
            demo_filename="admin.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="superuser",
            title="超级管理员功能",
            summary="仅超级管理员可见",
            aliases=(),
            trigger="#superuser",
            permission=Permission.SUPERUSER,
            demo_filename="superuser.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
    )

    # 管理员可以看到普通用户和管理员权限的功能
    admin_visible = filter_features_by_permission(features, Permission.GROUP_ADMIN)
    assert len(admin_visible) == 2
    assert {f.slug for f in admin_visible} == {"public", "admin"}


def test_filter_features_by_permission_superuser() -> None:
    """验证超级管理员可以看到所有功能"""
    from src.lib.plugin_docs import FeatureDoc, filter_features_by_permission

    features = (
        FeatureDoc(
            slug="public",
            title="公开功能",
            summary="所有人可见",
            aliases=(),
            trigger="#public",
            permission=Permission.NORMAL,
            demo_filename="public.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="admin",
            title="管理员功能",
            summary="仅管理员可见",
            aliases=(),
            trigger="#admin",
            permission=Permission.GROUP_ADMIN,
            demo_filename="admin.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
        FeatureDoc(
            slug="superuser",
            title="超级管理员功能",
            summary="仅超级管理员可见",
            aliases=(),
            trigger="#superuser",
            permission=Permission.SUPERUSER,
            demo_filename="superuser.png",
            overview="",
            preconditions="",
            flow_notes="",
            failures="",
            demo_turns=(),
        ),
    )

    # 超级管理员可以看到所有功能
    superuser_visible = filter_features_by_permission(features, Permission.SUPERUSER)
    assert len(superuser_visible) == 3
    assert {f.slug for f in superuser_visible} == {"public", "admin", "superuser"}


def test_can_view_node_respects_permission() -> None:
    """验证节点可见性检查遵守权限"""
    from src.lib.plugin_docs import DocNode, PluginDocBundle, can_view_node

    # 创建一个需要管理员权限的节点
    admin_node = DocNode(
        kind="plugin",
        slug="admin-plugin",
        parent_slug=None,
        category="admin",
        order=1,
        visible=True,
        hidden=False,
        internal=False,
        permission=Permission.GROUP_ADMIN,
        title="管理员插件",
        summary="仅管理员可见",
        description="",
        aliases=(),
        source_path=Path("test"),
        bundle=PluginDocBundle(
            title="测试",
            description="",
            summary="",
            trigger="",
            permission="",
            author="",
            version="",
            impression_color=DEFAULT_IMPRESSION_COLOR,
            index=(),
            source_path=Path("test"),
        ),
    )

    # 普通用户看不到
    assert not can_view_node(admin_node, Permission.NORMAL)

    # 管理员可以看到
    assert can_view_node(admin_node, Permission.GROUP_ADMIN)

    # 超级管理员可以看到
    assert can_view_node(admin_node, Permission.SUPERUSER)
