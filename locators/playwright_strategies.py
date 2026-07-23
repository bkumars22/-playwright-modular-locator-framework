from locators.modular_locator_framework import LocatorStrategy


class PlaywrightExactIdStrategy(LocatorStrategy):
    name = "playwright_exact_id"

    def __init__(self, page):
        self.page = page

    def find(self, dom, target):
        locator = self.page.locator(f"#{target}")
        if locator.count() > 0:
            return locator.first
        return None


class PlaywrightVisibleTextStrategy(LocatorStrategy):
    name = "playwright_visible_text"

    def __init__(self, page, expected_text):
        self.page = page
        self.expected_text = expected_text

    def find(self, dom, target):
        locator = self.page.get_by_text(self.expected_text)
        if locator.count() > 0:
            return locator.first
        return None


class PlaywrightRoleStrategy(LocatorStrategy):
    name = "playwright_role"

    def __init__(self, page, role, accessible_name):
        self.page = page
        self.role = role
        self.accessible_name = accessible_name

    def find(self, dom, target):
        locator = self.page.get_by_role(self.role, name=self.accessible_name)
        if locator.count() > 0:
            return locator.first
        return None
