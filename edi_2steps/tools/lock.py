import psycopg2

from odoo.exceptions import UserError


class Locker:
    """Try to grab a lock on a sequence dedicated to that usage through a context manager

    How to use that ?
    Just add some code inside a "with Locker(self.pool):"
    and the code will only be executed if the lock could be obtained.

    Why ?
    Useful to guarantee that some different processes (here cron tasks) never run at the same time.
    So if they can't grab the lock they will just stop & try next time.
    """

    def __init__(self, env, pool, sequence_name):
        """
        :param env: the environment
        :param pool: the registry
        :param sequence_name: the name of the sequence to lock (module_name.sequence_nam)
        """
        self.env = env
        self.new_cr = pool.cursor()
        self.sequence_name = sequence_name

    def __enter__(self):
        try:
            self.new_cr.execute(
                """
                SELECT id
                FROM ir_sequence
                WHERE name = %s;
            """,
                [self.sequence_name],
            )
            sequence_id = self.new_cr.fetchone()
            if not sequence_id:
                self.new_cr.close()
                raise UserError(self.env._("No lock to grab : sequence %s doesn't exist", self.sequence_name))

            self.new_cr.execute("SELECT 1 FROM ir_sequence WHERE id=%s FOR UPDATE NOWAIT", [sequence_id[0]])
        except psycopg2.OperationalError:
            self.new_cr.close()
            raise UserError(self.env._("Another process is already working on locker %s", self.sequence_name))
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.new_cr.commit()
        self.new_cr.close()
        return
