"""Zarzadzanie czlonkami (H-2): usuwanie, zmiana roli, cofanie zaproszen; skutki uboczne i granice uprawnien."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.ids import new_ulid
from app.core.config import Settings
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    login_via_magic_link,
    seed_key,
    seed_member,
    seed_org,
    seed_session,
    user_headers,
    with_db,
)


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def member_url(org_id: str, user_id: str) -> str:
    return f"{API}/orgs/{org_id}/members/{user_id}"


def audit(settings: Settings, action: str) -> list[models.AuditEvent]:
    return get_rows(settings, models.AuditEvent, models.AuditEvent.action == action)


def memberships(settings: Settings, org_id: str) -> dict[str, str]:
    return {m.user_id: m.role for m in get_rows(settings, models.Membership, models.Membership.org_id == org_id)}


# ================================================================ usuwanie czlonka
def test_owner_removes_a_member_and_all_their_access_ends(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "ola@example.test", "member")
    cookie, _csrf = seed_session(cloud_settings, uid)
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    seed_member(cloud_settings, other.org_id, "ola@example.test", "member")
    ola_keys = []

    async def mk(db):
        from app.control import service

        for oid in (org.org_id, other.org_id):
            key, raw = await service.issue_api_key(db, org_id=oid, name="ola", scopes=[sc.RUNS_READ], created_by=uid)
            ola_keys.append((oid, key.id, raw))

    with_db(cloud_settings, mk)

    async def bind(db):
        from sqlalchemy import update

        await db.execute(update(models.Session).where(models.Session.user_id == uid).values(org_id=org.org_id))

    with_db(cloud_settings, bind)
    r = cloud_client.delete(member_url(org.org_id, uid), headers=key_headers(org.api_key))
    assert r.status_code == 204, r.text
    assert uid not in memberships(cloud_settings, org.org_id)
    assert uid in memberships(cloud_settings, other.org_id)                       # inna organizacja bez zmian
    assert not get_rows(cloud_settings, models.Session, models.Session.user_id == uid)
    keys = {k.id: k for k in get_rows(cloud_settings, models.ApiKey, models.ApiKey.created_by == uid)}
    mine, theirs = ola_keys
    assert keys[mine[1]].revoked_at is not None and keys[theirs[1]].revoked_at is None
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/jobs", headers=key_headers(mine[2])).status_code == 401
    assert cloud_client.get(f"{API}/orgs/{other.org_id}/jobs", headers=key_headers(theirs[2])).status_code == 200
    assert cookie  # sesja istniala przed usunieciem
    (removed,) = audit(cloud_settings, "member.removed")
    assert removed.org_id == org.org_id and removed.target == f"user:{uid}" and "ola@" not in str(removed.meta)
    (revoked,) = [e for e in audit(cloud_settings, "key.revoked") if e.meta.get("reason") == "member_removed"]
    assert revoked.meta["key_prefix"] and revoked.org_id == org.org_id


def test_removal_by_a_user_session_works_and_is_attributed(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "ola@example.test", "member")
    r = cloud_client.delete(member_url(org.org_id, uid), headers=user_headers(cloud_settings, org.user_id))
    assert r.status_code == 204
    (event,) = audit(cloud_settings, "member.removed")
    assert event.actor_type == "user" and event.actor_id == org.user_id


def test_admin_can_remove_members_and_admins_but_not_owners(cloud_client, cloud_settings, org):
    admin_id = seed_member(cloud_settings, org.org_id, "adm@example.test", "admin")
    admin = user_headers(cloud_settings, admin_id)
    member_id = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    other_admin = seed_member(cloud_settings, org.org_id, "adm2@example.test", "admin")
    assert cloud_client.delete(member_url(org.org_id, org.user_id), headers=admin).status_code == 403
    assert cloud_client.delete(member_url(org.org_id, member_id), headers=admin).status_code == 204
    assert cloud_client.delete(member_url(org.org_id, other_admin), headers=admin).status_code == 204
    assert memberships(cloud_settings, org.org_id) == {org.user_id: "owner", admin_id: "admin"}


def test_plain_member_cannot_remove_anyone(cloud_client, cloud_settings, org):
    mid = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    other = seed_member(cloud_settings, org.org_id, "m2@example.test", "member")
    r = cloud_client.delete(member_url(org.org_id, other), headers=user_headers(cloud_settings, mid))
    assert r.status_code == 403
    assert other in memberships(cloud_settings, org.org_id)


def test_a_key_without_the_scope_cannot_remove_members(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    _, narrow = seed_key(cloud_settings, org.org_id, scopes=[sc.ORG_READ])
    assert cloud_client.delete(member_url(org.org_id, uid), headers=key_headers(narrow)).status_code == 403
    _, manage = seed_key(cloud_settings, org.org_id, scopes=[sc.MEMBERS_MANAGE])
    assert cloud_client.delete(member_url(org.org_id, uid), headers=key_headers(manage)).status_code == 204


def test_an_api_key_cannot_remove_an_owner(cloud_client, cloud_settings, org):
    other_owner = seed_member(cloud_settings, org.org_id, "o2@example.test", "owner")
    r = cloud_client.delete(member_url(org.org_id, other_owner), headers=key_headers(org.api_key))
    assert r.status_code == 403


def test_the_last_owner_cannot_be_removed(cloud_client, cloud_settings, org):
    r = cloud_client.delete(member_url(org.org_id, org.user_id), headers=user_headers(cloud_settings, org.user_id))
    assert r.status_code == 409 and r.json()["code"] == "last_owner"
    assert memberships(cloud_settings, org.org_id) == {org.user_id: "owner"}
    assert not audit(cloud_settings, "member.removed")


def test_an_owner_can_leave_when_another_owner_exists(cloud_client, cloud_settings, org):
    second = seed_member(cloud_settings, org.org_id, "o2@example.test", "owner")
    r = cloud_client.delete(member_url(org.org_id, org.user_id), headers=user_headers(cloud_settings, org.user_id))
    assert r.status_code == 204
    assert memberships(cloud_settings, org.org_id) == {second: "owner"}
    again = cloud_client.delete(member_url(org.org_id, second), headers=user_headers(cloud_settings, second))
    assert again.status_code == 409 and again.json()["code"] == "last_owner"


def test_removing_an_unknown_or_foreign_user_is_404(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    hdr = key_headers(org.api_key)
    assert cloud_client.delete(member_url(org.org_id, new_ulid()), headers=hdr).status_code == 404
    assert cloud_client.delete(member_url(org.org_id, other.user_id), headers=hdr).status_code == 404
    assert other.user_id in memberships(cloud_settings, other.org_id)


def test_removing_twice_is_404_the_second_time(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    hdr = key_headers(org.api_key)
    assert cloud_client.delete(member_url(org.org_id, uid), headers=hdr).status_code == 204
    assert cloud_client.delete(member_url(org.org_id, uid), headers=hdr).status_code == 404


def test_a_removed_member_cannot_sign_back_in_to_the_org(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "back@example.test", "member")
    cloud_client.delete(member_url(org.org_id, uid), headers=key_headers(org.api_key))
    body = login_via_magic_link(cloud_client, cloud_settings, "back@example.test")
    assert org.org_id not in [o["org_id"] for o in body["orgs"]]


# ================================================================ zmiana roli
def patch(client: TestClient, org_id: str, user_id: str, role, headers):
    return client.patch(member_url(org_id, user_id), json={"role": role}, headers=headers)


def test_owner_changes_a_role_and_it_is_audited(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    owner = user_headers(cloud_settings, org.user_id)
    r = patch(cloud_client, org.org_id, uid, "admin", owner)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin" and r.json()["user_id"] == uid and r.json()["email"] == "m@example.test"
    assert memberships(cloud_settings, org.org_id)[uid] == "admin"
    (event,) = audit(cloud_settings, "member.role_changed")
    assert event.meta == {"role": "admin"} and event.target == f"user:{uid}"


def test_same_role_is_a_noop_without_audit(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    assert patch(cloud_client, org.org_id, uid, "member", user_headers(cloud_settings, org.user_id)).status_code == 200
    assert not audit(cloud_settings, "member.role_changed")


@pytest.mark.parametrize("role", ["admin", "member"])
def test_only_an_owner_changes_roles(cloud_client, cloud_settings, org, role):
    actor = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, f"{role}@example.test", role))
    uid = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    assert patch(cloud_client, org.org_id, uid, "admin", actor).status_code == 403
    assert memberships(cloud_settings, org.org_id)[uid] == "member"


def test_an_api_key_cannot_change_roles(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    assert patch(cloud_client, org.org_id, uid, "admin", key_headers(org.api_key)).status_code == 403


def test_the_last_owner_cannot_be_demoted_but_one_of_two_can(cloud_client, cloud_settings, org):
    owner = user_headers(cloud_settings, org.user_id)
    r = patch(cloud_client, org.org_id, org.user_id, "admin", owner)
    assert r.status_code == 409 and r.json()["code"] == "last_owner"
    second = seed_member(cloud_settings, org.org_id, "o2@example.test", "owner")
    assert patch(cloud_client, org.org_id, second, "member", owner).status_code == 200
    assert patch(cloud_client, org.org_id, org.user_id, "admin", owner).status_code == 409


def test_promoting_to_owner_works_for_an_owner(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "m@example.test", "admin")
    assert patch(cloud_client, org.org_id, uid, "owner", user_headers(cloud_settings, org.user_id)).status_code == 200


@pytest.mark.parametrize("role", ["root", "", None, 5, "OWNER"])
def test_role_validation(cloud_client, cloud_settings, org, role):
    uid = seed_member(cloud_settings, org.org_id, "m@example.test", "member")
    assert patch(cloud_client, org.org_id, uid, role, user_headers(cloud_settings, org.user_id)).status_code == 422


def test_role_change_of_an_unknown_or_foreign_user_is_404(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    owner = user_headers(cloud_settings, org.user_id)
    assert patch(cloud_client, org.org_id, new_ulid(), "member", owner).status_code == 404
    assert patch(cloud_client, org.org_id, other.user_id, "member", owner).status_code == 404
    assert memberships(cloud_settings, other.org_id)[other.user_id] == "owner"


def test_demotion_takes_effect_immediately(cloud_client, cloud_settings, org):
    uid = seed_member(cloud_settings, org.org_id, "adm@example.test", "admin")
    hdr = user_headers(cloud_settings, uid)
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/audit", headers=hdr).status_code == 200
    patch(cloud_client, org.org_id, uid, "member", user_headers(cloud_settings, org.user_id))
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/audit", headers=hdr).status_code == 403


# ================================================================ zaproszenia
def invite(client, org_id, email, headers, role="member"):
    return client.post(f"{API}/orgs/{org_id}/invitations", json={"email": email, "role": role}, headers=headers)


def invitations(settings: Settings, org_id: str) -> list[models.Invitation]:
    return get_rows(settings, models.Invitation, models.Invitation.org_id == org_id)


def test_pending_invitations_can_be_listed_and_revoked(cloud_client, cloud_settings, org):
    hdr = key_headers(org.api_key)
    assert invite(cloud_client, org.org_id, "n@example.test", hdr).status_code == 202
    listed = cloud_client.get(f"{API}/orgs/{org.org_id}/invitations", headers=hdr)
    assert listed.status_code == 200
    (item,) = listed.json()
    assert item["email"] == "n@example.test" and item["role"] == "member" and item["id"]
    r = cloud_client.delete(f"{API}/orgs/{org.org_id}/invitations/{item['id']}", headers=hdr)
    assert r.status_code == 204
    assert not invitations(cloud_settings, org.org_id)
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/invitations", headers=hdr).json() == []
    (event,) = audit(cloud_settings, "invitation.revoked")
    assert event.target == f"invitation:{item['id']}" and "n@example" not in str(event.meta)


def test_a_revoked_invitation_is_not_accepted_at_sign_in(cloud_client, cloud_settings, org):
    hdr = key_headers(org.api_key)
    invite(cloud_client, org.org_id, "joiner@example.test", hdr, role="admin")
    (inv,) = invitations(cloud_settings, org.org_id)
    cloud_client.delete(f"{API}/orgs/{org.org_id}/invitations/{inv.id}", headers=hdr)
    body = login_via_magic_link(cloud_client, cloud_settings, "joiner@example.test")
    assert org.org_id not in [o["org_id"] for o in body["orgs"]]


def test_revoking_unknown_foreign_or_repeated_invitations_is_404(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    invite(cloud_client, other.org_id, "x@example.test", key_headers(other.api_key))
    (foreign,) = invitations(cloud_settings, other.org_id)
    hdr = key_headers(org.api_key)
    assert cloud_client.delete(f"{API}/orgs/{org.org_id}/invitations/{foreign.id}", headers=hdr).status_code == 404
    assert cloud_client.delete(f"{API}/orgs/{org.org_id}/invitations/{new_ulid()}", headers=hdr).status_code == 404
    assert len(invitations(cloud_settings, other.org_id)) == 1
    invite(cloud_client, org.org_id, "n@example.test", hdr)
    (mine,) = invitations(cloud_settings, org.org_id)
    assert cloud_client.delete(f"{API}/orgs/{org.org_id}/invitations/{mine.id}", headers=hdr).status_code == 204
    assert cloud_client.delete(f"{API}/orgs/{org.org_id}/invitations/{mine.id}", headers=hdr).status_code == 404


def test_an_accepted_invitation_cannot_be_revoked(cloud_client, cloud_settings, org):
    hdr = key_headers(org.api_key)
    invite(cloud_client, org.org_id, "joiner@example.test", hdr)
    (inv,) = invitations(cloud_settings, org.org_id)
    login_via_magic_link(cloud_client, cloud_settings, "joiner@example.test")
    assert cloud_client.delete(f"{API}/orgs/{org.org_id}/invitations/{inv.id}", headers=hdr).status_code == 404
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/invitations", headers=hdr).json() == []


def test_invitation_management_needs_the_scope(cloud_client, cloud_settings, org):
    invite(cloud_client, org.org_id, "n@example.test", key_headers(org.api_key))
    (inv,) = invitations(cloud_settings, org.org_id)
    member = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, "m@example.test", "member"))
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/invitations", headers=member).status_code == 403
    assert cloud_client.delete(f"{API}/orgs/{org.org_id}/invitations/{inv.id}", headers=member).status_code == 403
    assert len(invitations(cloud_settings, org.org_id)) == 1


# ================================================================ LOW-5: atomowy strzal `last_owner` (TOCTOU)
def two_owners(settings: Settings, org) -> tuple[str, str]:
    second = seed_member(settings, org.org_id, "owner2@example.test", "owner")
    return org.user_id, second


def test_guarded_delete_second_stale_request_removes_nothing(cloud_client, cloud_settings, org):
    """Dwa rownolegle zadania widzialy 2 ownerow (nieaktualny stan); drugie musi zostac odrzucone przez SQL."""
    from sqlalchemy import func, select

    from app.control import service
    from app.control.db import create_engine, make_sessionmaker

    a, b = two_owners(cloud_settings, org)

    async def scenario():
        engine = create_engine(cloud_settings)
        try:
            maker = make_sessionmaker(engine)
            async with maker() as s1, maker() as s2:
                count = select(func.count()).select_from(models.Membership).where(
                    models.Membership.org_id == org.org_id, models.Membership.role == "owner")
                assert (await s1.execute(count)).scalar_one() == 2    # oba zadania przeszly "sprawdzenie"
                assert (await s2.execute(count)).scalar_one() == 2
                first = await service.delete_member_guarded(s1, org.org_id, a)
                await s1.commit()
                second = await service.delete_member_guarded(s2, org.org_id, b)
                await s2.commit()
                return first, second
        finally:
            await engine.dispose()

    from tests.v1_helpers import run

    assert run(scenario()) == (True, False)
    assert memberships(cloud_settings, org.org_id) == {b: "owner"}


def test_guarded_demotion_second_stale_request_changes_nothing(cloud_client, cloud_settings, org):
    from app.control import service
    from app.control.db import create_engine, make_sessionmaker
    from tests.v1_helpers import run

    a, b = two_owners(cloud_settings, org)

    async def scenario():
        engine = create_engine(cloud_settings)
        try:
            maker = make_sessionmaker(engine)
            async with maker() as s1, maker() as s2:
                first = await service.set_member_role_guarded(s1, org.org_id, a, "member")
                await s1.commit()
                second = await service.set_member_role_guarded(s2, org.org_id, b, "admin")
                await s2.commit()
                return first, second
        finally:
            await engine.dispose()

    assert run(scenario()) == (True, False)
    assert sorted(memberships(cloud_settings, org.org_id).values()) == ["member", "owner"]


def test_guarded_helpers_allow_non_owners_and_promotions_regardless_of_owner_count(cloud_client, cloud_settings, org):
    from app.control import service

    uid = seed_member(cloud_settings, org.org_id, "ola@example.test", "member")

    async def go(db):
        assert await service.set_member_role_guarded(db, org.org_id, uid, "owner") is True    # awans
        assert await service.set_member_role_guarded(db, org.org_id, uid, "admin") is True    # 2 ownerow: degradacja ok
        assert await service.delete_member_guarded(db, org.org_id, uid) is True               # nie-owner
        assert await service.delete_member_guarded(db, org.org_id, uid) is False              # juz nie istnieje
        assert await service.set_member_role_guarded(db, org.org_id, "nope", "member") is False

    with_db(cloud_settings, go)
    assert memberships(cloud_settings, org.org_id) == {org.user_id: "owner"}


def test_api_sequence_never_leaves_the_org_without_an_owner(cloud_client, cloud_settings, org):
    a, b = two_owners(cloud_settings, org)
    as_a = user_headers(cloud_settings, a)
    first = cloud_client.delete(member_url(org.org_id, b), headers=as_a)
    second = cloud_client.delete(member_url(org.org_id, a), headers=as_a)
    assert first.status_code == 204
    assert second.status_code == 409 and second.json()["code"] == "last_owner"
    demote = cloud_client.patch(member_url(org.org_id, a), json={"role": "member"}, headers=as_a)
    assert demote.status_code == 409 and demote.json()["code"] == "last_owner"
    assert memberships(cloud_settings, org.org_id) == {a: "owner"}
    unknown = cloud_client.delete(member_url(org.org_id, new_ulid()), headers=as_a)
    assert unknown.status_code == 404                 # brak czlonka to 404, nie 409
    assert [e.meta for e in audit(cloud_settings, "member.removed")] == [{"role": "owner", "count": 0}]
