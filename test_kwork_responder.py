import os
import tempfile
import unittest

os.environ.setdefault("KWORK_SPHERES", "Python")
os.environ.setdefault("KWORK_SKILLS", "FastAPI")
os.environ.setdefault("KWORK_EXCLUDE", "wordpress")

from kwork_responder_mcp import Store, _extract_state_projects, _match, _price


class ResponderTests(unittest.TestCase):
    def test_minimum_price(self):
        self.assertEqual(_price("5 000–20 000 ₽"), 5000)
        self.assertEqual(_price("15 000 - 40 000"), 15000)

    def test_matching_and_exclusion(self):
        sphere, skills = _match({"name": "Python FastAPI", "description": "бот", "offers_count": 5})
        self.assertEqual((sphere, skills), ("Python", ["FastAPI"]))
        self.assertEqual(_match({"name": "Python WordPress", "description": "", "offers_count": 1}), (None, []))
        self.assertEqual(_match({"name": "Python", "description": "", "offers_count": 6}), (None, []))

    def test_store_deduplicates(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            store = Store(f.name)
            row = {"project_id": "1", "name": "x", "sphere": "Python", "min_price": 1,
                   "max_price": 2, "sent_price": 1, "response": "r", "created_at": "now", "status": "draft"}
            store.save(row)
            store.save(row)
            self.assertTrue(store.seen("1"))
            self.assertEqual(store.db.execute("select count(*) from responses").fetchone()[0], 1)

    def test_extract_state_projects(self):
        html = '<script>window.stateData = {"wantsListData":{"pagination":{"data":[{"id":7,"name":"X"}]}}};</script>'
        self.assertEqual(_extract_state_projects(html)[0]["id"], 7)


if __name__ == "__main__":
    unittest.main()
