"""Tests for Game Master actions."""

from __future__ import annotations

import unittest

from pzctl import gm


class FakeSupervisor:
    def __init__(self, alive: bool = True, reply=(True, "OK")):
        self._alive = alive
        self.reply = reply
        self.sent: list[str] = []
        self.emitted: list[tuple[str, str]] = []

    def is_alive(self) -> bool:
        return self._alive

    def emit(self, text: str, stream: str = "pzctl") -> None:
        self.emitted.append((text, stream))

    def send_command(self, cmd: str, prefer: str = "auto", echo_as=None):
        self.sent.append(cmd)
        return self.reply


class AddXpTests(unittest.TestCase):
    def test_sends_the_documented_command(self):
        sup = FakeSupervisor()
        result = gm.add_xp(sup, "rj", "Sprinting", 500)
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent, ['addxp "rj" Sprinting=500'])

    def test_sends_the_id_not_the_display_name(self):
        """The point of the picker: Running is sent as Sprinting."""
        sup = FakeSupervisor()
        gm.add_xp(sup, "rj", "Sprinting", 100)
        self.assertIn("Sprinting=100", sup.sent[0])
        self.assertNotIn("Running", sup.sent[0])

    def test_negative_xp_is_allowed(self):
        """Removing XP is a legitimate correction."""
        sup = FakeSupervisor()
        self.assertTrue(gm.add_xp(sup, "rj", "Fitness", -250)["ok"])
        self.assertEqual(sup.sent, ['addxp "rj" Fitness=-250'])

    def test_zero_is_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.add_xp(sup, "rj", "Fitness", 0)["ok"])
        self.assertEqual(sup.sent, [])

    def test_non_numeric_xp_refused(self):
        sup = FakeSupervisor()
        for bad in ("lots", None, "", "1e9999"):
            self.assertFalse(gm.add_xp(sup, "rj", "Fitness", bad)["ok"], repr(bad))
        self.assertEqual(sup.sent, [])

    def test_absurd_xp_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.add_xp(sup, "rj", "Fitness", 10**12)["ok"])
        self.assertEqual(sup.sent, [])

    def test_string_number_accepted(self):
        """The panel sends JSON; a numeric string must still work."""
        sup = FakeSupervisor()
        self.assertTrue(gm.add_xp(sup, "rj", "Fitness", "250")["ok"])

    def test_missing_perk_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.add_xp(sup, "rj", "", 100)["ok"])
        self.assertEqual(sup.sent, [])

    def test_perk_injection_refused(self):
        """The perk goes onto a command line unquoted."""
        sup = FakeSupervisor()
        for bad in ('Fitness=1" ; quit', "Fitness quit", "Fit\nness", "-flag"):
            self.assertFalse(gm.add_xp(sup, "rj", bad, 100)["ok"], bad)
        self.assertEqual(sup.sent, [])

    def test_username_injection_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.add_xp(sup, 'rj" "x', "Fitness", 100)["ok"])
        self.assertEqual(sup.sent, [])

    def test_unknown_perk_is_sent_for_the_server_to_judge(self):
        """pzctl does not decide which ids are real; the server does."""
        sup = FakeSupervisor()
        self.assertTrue(gm.add_xp(sup, "rj", "SomeFutureSkill", 100)["ok"])
        self.assertIn("SomeFutureSkill=100", sup.sent[0])

    def test_requires_a_running_server(self):
        sup = FakeSupervisor(alive=False)
        result = gm.add_xp(sup, "rj", "Fitness", 100)
        self.assertFalse(result["ok"])
        self.assertEqual(sup.sent, [])

    def test_is_logged_for_audit(self):
        sup = FakeSupervisor()
        gm.add_xp(sup, "rj", "Fitness", 100)
        self.assertTrue(any("gm: granting" in text for text, _ in sup.emitted))

    def test_failure_logged_as_error(self):
        sup = FakeSupervisor(reply=(False, "rcon down"))
        gm.add_xp(sup, "rj", "Fitness", 100)
        self.assertTrue(any(stream == "error" for _, stream in sup.emitted))

    def test_reply_is_reported(self):
        sup = FakeSupervisor(reply=(True, "XP added"))
        self.assertEqual(gm.add_xp(sup, "rj", "Fitness", 100)["reply"], "XP added")


if __name__ == "__main__":
    unittest.main()


class AddItemTests(unittest.TestCase):
    def test_sends_the_documented_command(self):
        sup = FakeSupervisor()
        result = gm.add_item(sup, "rj", "Base.Axe", 2)
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent, ['additem "rj" "Base.Axe" 2'])

    def test_defaults_to_one(self):
        sup = FakeSupervisor()
        gm.add_item(sup, "rj", "Base.Axe")
        self.assertEqual(sup.sent, ['additem "rj" "Base.Axe" 1'])

    def test_requires_module_qualified_id(self):
        """additem takes Module.ItemName; a bare name will not work."""
        sup = FakeSupervisor()
        self.assertFalse(gm.add_item(sup, "rj", "Axe")["ok"])
        self.assertEqual(sup.sent, [])

    def test_rejects_injection_in_the_item_id(self):
        sup = FakeSupervisor()
        for bad in ('Base.Axe" ; quit', "Base.Axe quit", "Base.Axe\nquit", "../Base.Axe"):
            self.assertFalse(gm.add_item(sup, "rj", bad)["ok"], bad)
        self.assertEqual(sup.sent, [])

    def test_rejects_injection_in_the_username(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.add_item(sup, 'rj" "x', "Base.Axe")["ok"])
        self.assertEqual(sup.sent, [])

    def test_count_must_be_positive(self):
        sup = FakeSupervisor()
        for bad in (0, -5):
            self.assertFalse(gm.add_item(sup, "rj", "Base.Axe", bad)["ok"], bad)
        self.assertEqual(sup.sent, [])

    def test_absurd_count_refused(self):
        """A huge spawn is far more likely a typo than an intention."""
        sup = FakeSupervisor()
        self.assertFalse(gm.add_item(sup, "rj", "Base.Axe", 999999)["ok"])
        self.assertEqual(sup.sent, [])

    def test_non_numeric_count_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.add_item(sup, "rj", "Base.Axe", "many")["ok"])

    def test_string_count_accepted(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.add_item(sup, "rj", "Base.Axe", "3")["ok"])

    def test_unknown_item_is_sent_for_the_server_to_judge(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.add_item(sup, "rj", "SomeMod.NewThing")["ok"])

    def test_requires_a_running_server(self):
        sup = FakeSupervisor(alive=False)
        self.assertFalse(gm.add_item(sup, "rj", "Base.Axe")["ok"])
        self.assertEqual(sup.sent, [])

    def test_is_logged_for_audit(self):
        sup = FakeSupervisor()
        gm.add_item(sup, "rj", "Base.Axe", 2)
        self.assertTrue(any("gm: giving 2x Base.Axe" in text for text, _ in sup.emitted))

    def test_failure_logged_as_error(self):
        sup = FakeSupervisor(reply=(False, "nope"))
        gm.add_item(sup, "rj", "Base.Axe")
        self.assertTrue(any(stream == "error" for _, stream in sup.emitted))
