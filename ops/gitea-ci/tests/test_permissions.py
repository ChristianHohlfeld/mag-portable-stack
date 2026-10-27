"""Pinned Gitea permission ceiling: exactly one repo, no other state changes."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import json,pathlib,sqlite3,unittest
ROOT=pathlib.Path(__file__).resolve().parents[3]
SQL=(ROOT/'ops/gitea-ci/token-permissions.sql').read_text()
class Permissions(unittest.TestCase):
 def db(self):
  db=sqlite3.connect(':memory:');db.executescript('''CREATE TABLE user(id INTEGER PRIMARY KEY,lower_name TEXT,type INTEGER,is_admin INTEGER);
CREATE TABLE repository(id INTEGER PRIMARY KEY,owner_id INTEGER,lower_name TEXT);
CREATE TABLE repo_unit(repo_id INTEGER,type INTEGER,config TEXT);
CREATE TABLE user_setting(user_id INTEGER,setting_key TEXT,setting_value TEXT);
INSERT INTO user VALUES(1,'mag-ci',4,0),(2,'chris',0,1);
INSERT INTO repository VALUES(10,1,'mag-portable-stack'),(20,2,'unrelated');
INSERT INTO repo_unit VALUES(10,10,'{"DisabledWorkflows":["old.yml"],"override_owner_config":true}'),(20,10,'{"keep":"exact"}');
INSERT INTO user_setting VALUES(2,'actions.config','{"keep":"exact"}');''');return db
 def test_ceiling_is_idempotent_and_only_scoped_owner_repo_changes(self):
  db=self.db();db.executescript(SQL)
  settings=json.loads(db.execute("SELECT setting_value FROM user_setting WHERE user_id=1").fetchone()[0]);self.assertEqual(settings['max_token_permissions'],{'unit_access_modes':{'1':1}})
  config=json.loads(db.execute('SELECT config FROM repo_unit WHERE repo_id=10').fetchone()[0]);self.assertFalse(config['override_owner_config']);self.assertEqual(config['DisabledWorkflows'],['old.yml'])
  before=list(db.iterdump());db.execute('DROP TABLE ci_assert');db.executescript(SQL);self.assertEqual(before,list(db.iterdump()))
  self.assertEqual(db.execute('SELECT config FROM repo_unit WHERE repo_id=20').fetchone()[0],'{"keep":"exact"}')
  self.assertEqual(db.execute('SELECT setting_value FROM user_setting WHERE user_id=2').fetchone()[0],'{"keep":"exact"}')
 def test_unrelated_second_repo_or_admin_owner_aborts(self):
  for statement in ["INSERT INTO repository VALUES(11,1,'other')","UPDATE user SET is_admin=1 WHERE id=1"]:
   db=self.db();db.execute(statement)
   with self.assertRaises(sqlite3.IntegrityError):db.executescript(SQL)
   db.rollback();self.assertEqual(db.execute('SELECT count(*) FROM user_setting WHERE user_id=1').fetchone()[0],0)
if __name__=='__main__':unittest.main(verbosity=2)
