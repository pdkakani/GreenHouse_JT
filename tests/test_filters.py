import unittest

from filters import is_usa_location, is_software_role


class FilterTests(unittest.TestCase):
    def test_usa_location_accepts_remote(self):
        self.assertTrue(is_usa_location("Remote"))

    def test_usa_location_rejects_non_us_remote(self):
        self.assertFalse(is_usa_location("Remote - India"))

    def test_usa_location_accepts_us_city(self):
        self.assertTrue(is_usa_location("San Francisco, CA"))

    def test_software_role_accepts_engineering_title(self):
        self.assertTrue(is_software_role("Senior Backend Engineer", "Engineering"))

    def test_software_role_rejects_non_technical_role(self):
        self.assertFalse(is_software_role("HR Specialist", "People"))


if __name__ == "__main__":
    unittest.main()
