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


class AddVehicleTests(unittest.TestCase):
    def test_spawns_for_a_player(self):
        sup = FakeSupervisor()
        result = gm.add_vehicle(sup, "Base.CarLightsPolice", "rj")
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent, ['addvehicle "Base.CarLightsPolice" "rj"'])

    def test_spawns_at_coordinates(self):
        """The command accepts a player name or an x,y,z triple."""
        sup = FakeSupervisor()
        result = gm.add_vehicle(sup, "Base.CarLightsPolice", "10700,9200,0")
        self.assertTrue(result["ok"])
        self.assertTrue(result["coords"])
        self.assertEqual(sup.sent, ['addvehicle "Base.CarLightsPolice" "10700,9200,0"'])

    def test_coordinates_may_have_spaces(self):
        sup = FakeSupervisor()
        gm.add_vehicle(sup, "Base.Car", "100, 200, 0")
        self.assertEqual(sup.sent, ['addvehicle "Base.Car" "100,200,0"'])

    def test_negative_and_decimal_coordinates(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.add_vehicle(sup, "Base.Car", "-10.5,200,0")["ok"])

    def test_requires_a_module_qualified_script(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.add_vehicle(sup, "CarLightsPolice", "rj")["ok"])
        self.assertEqual(sup.sent, [])

    def test_missing_target(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.add_vehicle(sup, "Base.Car", "")["ok"])
        self.assertEqual(sup.sent, [])

    def test_rejects_injection_in_the_script(self):
        sup = FakeSupervisor()
        for bad in ('Base.Car" ; quit', "Base.Car quit", "Base.Car\nquit"):
            self.assertFalse(gm.add_vehicle(sup, bad, "rj")["ok"], bad)
        self.assertEqual(sup.sent, [])

    def test_rejects_injection_in_the_target(self):
        """A target that is neither valid coordinates nor a valid name."""
        sup = FakeSupervisor()
        for bad in ('rj" "x', "rj\nquit", "1,2"):
            self.assertFalse(gm.add_vehicle(sup, "Base.Car", bad)["ok"], bad)
        self.assertEqual(sup.sent, [])

    def test_requires_a_running_server(self):
        sup = FakeSupervisor(alive=False)
        self.assertFalse(gm.add_vehicle(sup, "Base.Car", "rj")["ok"])
        self.assertEqual(sup.sent, [])

    def test_is_logged_for_audit(self):
        sup = FakeSupervisor()
        gm.add_vehicle(sup, "Base.Car", "rj")
        self.assertTrue(any("gm: spawning Base.Car" in text for text, _ in sup.emitted))


class TeleportTests(unittest.TestCase):
    def test_player_to_player(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.teleport(sup, "rj", "bob")["ok"])
        self.assertEqual(sup.sent, ['teleport "rj" "bob"'])

    def test_to_coordinates_uses_teleportto(self):
        """teleportto is a different command taking only a position."""
        sup = FakeSupervisor()
        result = gm.teleport(sup, "rj", "10700,9200,0")
        self.assertTrue(result["coords"])
        self.assertEqual(sup.sent, ["teleportto 10700,9200,0"])

    def test_malformed_coordinates_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.teleport(sup, "rj", "1,2")["ok"])
        self.assertEqual(sup.sent, [])

    def test_injection_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.teleport(sup, 'rj" "x', "bob")["ok"])
        self.assertFalse(gm.teleport(sup, "rj", 'bob" ; quit')["ok"])
        self.assertEqual(sup.sent, [])

    def test_requires_running_server(self):
        self.assertFalse(gm.teleport(FakeSupervisor(alive=False), "rj", "bob")["ok"])


class WeatherTests(unittest.TestCase):
    def test_simple_events(self):
        for event in gm.SIMPLE_EVENTS:
            sup = FakeSupervisor()
            self.assertTrue(gm.weather(sup, event)["ok"], event)
            self.assertEqual(sup.sent, [event])

    def test_rain_takes_an_intensity(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.weather(sup, "startrain", 50)["ok"])
        self.assertEqual(sup.sent, ['startrain "50"'])

    def test_documented_ranges_enforced(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.weather(sup, "startrain", 0)["ok"])
        self.assertFalse(gm.weather(sup, "startrain", 101)["ok"])
        self.assertFalse(gm.weather(sup, "startstorm", 25)["ok"])
        self.assertEqual(sup.sent, [])

    def test_missing_value(self):
        self.assertFalse(gm.weather(FakeSupervisor(), "startrain")["ok"])

    def test_targeted_events_take_a_player(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.weather(sup, "lightning", "rj")["ok"])
        self.assertEqual(sup.sent, ['lightning "rj"'])

    def test_targeted_event_injection_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.weather(sup, "thunder", 'rj" ; quit')["ok"])
        self.assertEqual(sup.sent, [])

    def test_unknown_event(self):
        self.assertFalse(gm.weather(FakeSupervisor(), "tornado")["ok"])


class HordeTests(unittest.TestCase):
    def test_spawns(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.create_horde(sup, "rj", 10)["ok"])
        self.assertEqual(sup.sent, ['createhorde 10 "rj"'])

    def test_bounded(self):
        """Zombies cannot be un-spawned, so the cap is deliberate."""
        sup = FakeSupervisor()
        self.assertFalse(gm.create_horde(sup, "rj", 0)["ok"])
        self.assertFalse(gm.create_horde(sup, "rj", gm.MAX_HORDE + 1)["ok"])
        self.assertEqual(sup.sent, [])

    def test_cap_error_explains_why(self):
        result = gm.create_horde(FakeSupervisor(), "rj", 10000)
        self.assertIn("cannot be undone", result["error"])

    def test_injection_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.create_horde(sup, 'rj" "x', 5)["ok"])
        self.assertEqual(sup.sent, [])


class PlayerStateTests(unittest.TestCase):
    def test_enable(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.player_state(sup, "godmode", "rj", True)["ok"])
        self.assertEqual(sup.sent, ['godmode "rj" -true'])

    def test_disable(self):
        sup = FakeSupervisor()
        gm.player_state(sup, "noclip", "rj", False)
        self.assertEqual(sup.sent, ['noclip "rj" -false'])

    def test_all_documented_states(self):
        for state in gm.STATE_COMMANDS:
            sup = FakeSupervisor()
            self.assertTrue(gm.player_state(sup, state, "rj", True)["ok"], state)

    def test_unknown_state(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.player_state(sup, "flying", "rj", True)["ok"])
        self.assertEqual(sup.sent, [])

    def test_injection_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.player_state(sup, "godmode", 'rj" ; quit', True)["ok"])
        self.assertEqual(sup.sent, [])


class BroadcastTests(unittest.TestCase):
    def test_sends(self):
        sup = FakeSupervisor()
        self.assertTrue(gm.broadcast(sup, "server restarting")["ok"])
        self.assertEqual(sup.sent, ['servermsg "server restarting"'])

    def test_empty_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(gm.broadcast(sup, "   ")["ok"])
        self.assertEqual(sup.sent, [])

    def test_breakout_characters_stripped_not_rejected(self):
        """The message is free text an admin typed; keep it, make it safe."""
        sup = FakeSupervisor()
        self.assertTrue(gm.broadcast(sup, 'hello "world"\nquit')["ok"])
        # Quotes and the newline go; the rest of the text survives.
        self.assertEqual(sup.sent, ['servermsg "hello worldquit"'])

    def test_length_bounded(self):
        self.assertFalse(gm.broadcast(FakeSupervisor(), "x" * 600)["ok"])

    def test_requires_running_server(self):
        self.assertFalse(gm.broadcast(FakeSupervisor(alive=False), "hi")["ok"])
