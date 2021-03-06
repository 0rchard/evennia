"""
Player (OOC) commands. These are stored on the Player object
and self.caller is thus always a Player, not an Object/Character.

These commands go in the PlayerCmdset and are accessible also
when puppeting a Character (although with lower priority)

These commands use the MuxCommandOOC parent that makes sure
to setup caller correctly. They use self.player to make sure
to always use the player object rather than self.caller (which
change depending on the level you are calling from)
The property self.character can be used to
access the character when these commands are triggered with
a connected character (such as the case of the @ooc command), it
is None if we are OOC.

Note that under MULTISESSION_MODE > 2, Player- commands should use
self.msg() and similar methods to reroute returns to the correct
method. Otherwise all text will be returned to all connected sessions.

"""
from builtins import range

import time
from django.conf import settings
from evennia.server.sessionhandler import SESSIONS
from evennia.commands.default.muxcommand import MuxPlayerCommand
from evennia.utils import utils, create, search, prettytable

_MAX_NR_CHARACTERS = settings.MAX_NR_CHARACTERS
_MULTISESSION_MODE = settings.MULTISESSION_MODE

# limit symbol import for API
__all__ = ("CmdOOCLook", "CmdIC", "CmdOOC", "CmdPassword", "CmdQuit",
           "CmdCharCreate", "CmdOption", "CmdSessions", "CmdWho",
           "CmdColorTest", "CmdQuell")


class MuxPlayerLookCommand(MuxPlayerCommand):
    """
    Custom parent (only) parsing for OOC looking, sets a "playable"
    property on the command based on the parsing.

    """
    def parse(self):
        "Custom parsing"

        super(MuxPlayerLookCommand, self).parse()

        if _MULTISESSION_MODE < 2:
            # only one character allowed - not used in this mode
            self.playable = None
            return

        playable = self.player.db._playable_characters
        if playable is not None:
            # clean up list if character object was deleted in between
            if None in playable:
                playable = [character for character in playable if character]
                self.player.db._playable_characters = playable
        # store playable property
        if self.args:
            self.playable = dict((utils.to_str(char.key.lower()), char)
                         for char in playable).get(self.args.lower(), None)
        else:
            self.playable = playable


# Obs - these are all intended to be stored on the Player, and as such,
# use self.player instead of self.caller, just to be sure. Also self.msg()
# is used to make sure returns go to the right session

# note that this is inheriting from MuxPlayerLookCommand,
# and has the .playable property.
class CmdOOCLook(MuxPlayerLookCommand):
    """
    look while out-of-character

    Usage:
      look

    Look in the ooc state.
    """

    #This is an OOC version of the look command. Since a
    #Player doesn't have an in-game existence, there is no
    #concept of location or "self". If we are controlling
    #a character, pass control over to normal look.

    key = "look"
    aliases = ["l", "ls"]
    locks = "cmd:all()"
    help_category = "General"

    def func(self):
        "implement the ooc look command"

        if _MULTISESSION_MODE < 2:
            # only one character allowed
            self.msg("You are out-of-character (OOC).\nUse {w@ic{n to get back into the game.")
            return

        # call on-player look helper method
        self.msg(self.player.at_look(target=self.playable, session=self.session))


class CmdCharCreate(MuxPlayerCommand):
    """
    create a new character

    Usage:
      @charcreate <charname> [= desc]

    Create a new character, optionally giving it a description. You
    may use upper-case letters in the name - you will nevertheless
    always be able to access your character using lower-case letters
    if you want.
    """
    key = "@charcreate"
    locks = "cmd:pperm(Players)"
    help_category = "General"

    def func(self):
        "create the new character"
        player = self.player
        if not self.args:
            self.msg("Usage: @charcreate <charname> [= description]")
            return
        key = self.lhs
        desc = self.rhs

        charmax = _MAX_NR_CHARACTERS if _MULTISESSION_MODE > 1 else 1

        if not player.is_superuser and \
            (player.db._playable_characters and
                len(player.db._playable_characters) >= charmax):
            self.msg("You may only create a maximum of %i characters." % charmax)
            return
        # create the character
        from evennia.objects.models import ObjectDB

        start_location = ObjectDB.objects.get_id(settings.START_LOCATION)
        default_home = ObjectDB.objects.get_id(settings.DEFAULT_HOME)
        typeclass = settings.BASE_CHARACTER_TYPECLASS
        permissions = settings.PERMISSION_PLAYER_DEFAULT

        new_character = create.create_object(typeclass, key=key,
                                             location=start_location,
                                             home=default_home,
                                             permissions=permissions)
        # only allow creator (and immortals) to puppet this char
        new_character.locks.add("puppet:id(%i) or pid(%i) or perm(Immortals) or pperm(Immortals)" %
                                (new_character.id, player.id))
        player.db._playable_characters.append(new_character)
        if desc:
            new_character.db.desc = desc
        elif not new_character.db.desc:
            new_character.db.desc = "This is a Player."
        self.msg("Created new character %s. Use {w@ic %s{n to enter the game as this character." % (new_character.key, new_character.key))


class CmdIC(MuxPlayerCommand):
    """
    control an object you have permission to puppet

    Usage:
      @ic <character>

    Go in-character (IC) as a given Character.

    This will attempt to "become" a different object assuming you have
    the right to do so. Note that it's the PLAYER character that puppets
    characters/objects and which needs to have the correct permission!

    You cannot become an object that is already controlled by another
    player. In principle <character> can be any in-game object as long
    as you the player have access right to puppet it.
    """

    key = "@ic"
    # lock must be all() for different puppeted objects to access it.
    locks = "cmd:all()"
    aliases = "@puppet"
    help_category = "General"

    def func(self):
        """
        Main puppet method
        """
        player = self.player
        session = self.session

        new_character = None
        if not self.args:
            new_character = player.db._last_puppet
            if not new_character:
                self.msg("Usage: @ic <character>")
                return
        if not new_character:
            # search for a matching character
            new_character = search.object_search(self.args)
            if new_character:
                new_character = new_character[0]
            else:
                self.msg("That is not a valid character choice.")
                return
        try:
            player.puppet_object(session, new_character)
            player.db._last_puppet = new_character
        except RuntimeError as exc:
            self.msg("{rYou cannot become {C%s{n: %s" % (new_character.name, exc))


# note that this is inheriting from MuxPlayerLookCommand,
# and as such has the .playable property.
class CmdOOC(MuxPlayerLookCommand):
    """
    stop puppeting and go ooc

    Usage:
      @ooc

    Go out-of-character (OOC).

    This will leave your current character and put you in a incorporeal OOC state.
    """

    key = "@ooc"
    locks = "cmd:pperm(Players)"
    aliases = "@unpuppet"
    help_category = "General"

    def func(self):
        "Implement function"

        player = self.player
        session = self.session

        old_char = player.get_puppet(session)
        if not old_char:
            string = "You are already OOC."
            self.msg(string)
            return

        player.db._last_puppet = old_char

        # disconnect
        try:
            player.unpuppet_object(session)
            self.msg("\n{GYou go OOC.{n\n")

            if _MULTISESSION_MODE < 2:
                # only one character allowed
                self.msg("You are out-of-character (OOC).\nUse {w@ic{n to get back into the game.")
                return

            self.msg(player.at_look(target=self.playable, session=session))

        except RuntimeError as exc:
            self.msg("{rCould not unpuppet from {c%s{n: %s" % (old_char, exc))

class CmdSessions(MuxPlayerCommand):
    """
    check your connected session(s)

    Usage:
      @sessions

    Lists the sessions currently connected to your account.

    """
    key = "@sessions"
    locks = "cmd:all()"
    help_category = "General"

    def func(self):
        "Implement function"
        player = self.player
        sessions = player.sessions.all()

        table = prettytable.PrettyTable(["{wsessid",
                                         "{wprotocol",
                                         "{whost",
                                         "{wpuppet/character",
                                         "{wlocation"])
        for sess in sorted(sessions, key=lambda x: x.sessid):
            char = player.get_puppet(sess)
            table.add_row([str(sess.sessid), str(sess.protocol_key),
                           type(sess.address) == tuple and sess.address[0] or sess.address,
                           char and str(char) or "None",
                           char and str(char.location) or "N/A"])
        string = "{wYour current session(s):{n\n%s" % table
        self.msg(string)


class CmdWho(MuxPlayerCommand):
    """
    list who is currently online

    Usage:
      who
      doing

    Shows who is currently online. Doing is an alias that limits info
    also for those with all permissions.
    """

    key = "who"
    aliases = "doing"
    locks = "cmd:all()"

    def func(self):
        """
        Get all connected players by polling session.
        """

        player = self.player
        session_list = SESSIONS.get_sessions()

        session_list = sorted(session_list, key=lambda o: o.player.key)

        if self.cmdstring == "doing":
            show_session_data = False
        else:
            show_session_data = player.check_permstring("Immortals") or player.check_permstring("Wizards")

        nplayers = (SESSIONS.player_count())
        if show_session_data:
            # privileged info
            table = prettytable.PrettyTable(["{wPlayer Name",
                                             "{wOn for",
                                             "{wIdle",
                                             "{wPuppeting",
                                             "{wRoom",
                                             "{wCmds",
                                             "{wProtocol",
                                             "{wHost"])
            for session in session_list:
                if not session.logged_in: continue
                delta_cmd = time.time() - session.cmd_last_visible
                delta_conn = time.time() - session.conn_time
                player = session.get_player()
                puppet = session.get_puppet()
                location = puppet.location.key if puppet else "None"
                table.add_row([utils.crop(player.name, width=25),
                               utils.time_format(delta_conn, 0),
                               utils.time_format(delta_cmd, 1),
                               utils.crop(puppet.key if puppet else "None", width=25),
                               utils.crop(location, width=25),
                               session.cmd_total,
                               session.protocol_key,
                               isinstance(session.address, tuple) and session.address[0] or session.address])
        else:
            # unprivileged
            table = prettytable.PrettyTable(["{wPlayer name", "{wOn for", "{wIdle"])
            for session in session_list:
                if not session.logged_in:
                    continue
                delta_cmd = time.time() - session.cmd_last_visible
                delta_conn = time.time() - session.conn_time
                player = session.get_player()
                table.add_row([utils.crop(player.key, width=25),
                               utils.time_format(delta_conn, 0),
                               utils.time_format(delta_cmd, 1)])

        isone = nplayers == 1
        string = "{wPlayers:{n\n%s\n%s unique account%s logged in." % (table, "One" if isone else nplayers, "" if isone else "s")
        self.msg(string)


class CmdOption(MuxPlayerCommand):
    """
    Set an account option

    @option
    @option encoding = [encoding]
    @option screenreader = on|off

    The text encoding is mostly an issue only if you want to use
    non-ASCII characters (i.e. letters/symbols not found in English).
    If you see that your characters look strange (or you get encoding
    errors), you should use this command to set the server encoding to
    be the same used in your client program. If given the empty string
    (default), the custom encoding will be removed and only Evennia's
    defaults will be used.

    The screenreader setting strips the text output for users using
    screen readers. It strips based on settings.SCREENREADER_REGEX_STRIP.

    """
    key = "@option"
    aliases = "@options"
    locks = "cmd:all()"

    def func(self):
        """
        Implements the command
        """
        if self.session is None:
            return

        if not self.args:
            # list the option settings
            string = "{wEncoding{n:\n"
            pencoding = self.session.encoding or "None"
            sencodings = settings.ENCODINGS
            string += " Custom: %s\n Server: %s" % (pencoding, ", ".join(sencodings))
            string += "\n{wScreen Reader mode:{n %s" % self.session.screenreader
            self.caller.msg(string)
            return

        if not self.rhs:
            self.caller.msg("Usage: @option [name = [value]]")

        if self.lhs == "encoding":
            # change encoding
            old_encoding = self.session.encoding
            new_encoding = self.rhs.strip() or "utf-8"
            self.session.encoding = new_encoding
            self.caller.msg("Encoding was changed from %s to %s." % (old_encoding, new_encoding))
            return

        if self.lhs == "screenreader":
            onoff = self.rhs.lower() == "on"
            self.session.screenreader = onoff
            self.caller.msg("Screen reader mode was turned {w%s{n." % ("on" if onoff else "off"))


class CmdPassword(MuxPlayerCommand):
    """
    change your password

    Usage:
      @password <old password> = <new password>

    Changes your password. Make sure to pick a safe one.
    """
    key = "@password"
    locks = "cmd:pperm(Players)"

    def func(self):
        "hook function."

        player = self.player
        if not self.rhs:
            self.msg("Usage: @password <oldpass> = <newpass>")
            return
        oldpass = self.lhslist[0]  # this is already stripped by parse()
        newpass = self.rhslist[0]  #               ''
        if not player.check_password(oldpass):
            self.msg("The specified old password isn't correct.")
        elif len(newpass) < 3:
            self.msg("Passwords must be at least three characters long.")
        else:
            player.set_password(newpass)
            player.save()
            self.msg("Password changed.")


class CmdQuit(MuxPlayerCommand):
    """
    quit the game

    Usage:
      @quit

    Switch:
      all - disconnect all connected sessions

    Gracefully disconnect your current session from the
    game. Use the /all switch to disconnect from all sessions.
    """
    key = "@quit"
    aliases = "quit"
    locks = "cmd:all()"

    def func(self):
        "hook function"
        player = self.player

        if 'all' in self.switches:
            player.msg("{RQuitting{n all sessions. Hope to see you soon again.", session=self.session)
            for session in player.sessions.all():
                player.disconnect_session_from_player(session)
        else:
            nsess = len(player.sessions.all())
            if nsess == 2:
                player.msg("{RQuitting{n. One session is still connected.", session=self.session)
            elif nsess > 2:
                player.msg("{RQuitting{n. %i session are still connected." % (nsess-1), session=self.session)
            else:
                # we are quitting the last available session
                player.msg("{RQuitting{n. Hope to see you again, soon.", session=self.session)
            player.disconnect_session_from_player(self.session)



class CmdColorTest(MuxPlayerCommand):
    """
    testing which colors your client support

    Usage:
      @color ansi|xterm256

    Prints a color map along with in-mud color codes to use to produce
    them.  It also tests what is supported in your client. Choices are
    16-color ansi (supported in most muds) or the 256-color xterm256
    standard. No checking is done to determine your client supports
    color - if not you will see rubbish appear.
    """
    key = "@color"
    aliases = "color"
    locks = "cmd:all()"
    help_category = "General"

    def table_format(self, table):
       """
       Helper method to format the ansi/xterm256 tables.
       Takes a table of columns [[val,val,...],[val,val,...],...]
       """
       if not table:
           return [[]]

       extra_space = 1
       max_widths = [max([len(str(val)) for val in col]) for col in table]
       ftable = []
       for irow in range(len(table[0])):
           ftable.append([str(col[irow]).ljust(max_widths[icol]) + " " * extra_space
                          for icol, col in enumerate(table)])
       return ftable

    def func(self):
        "Show color tables"

        if self.args.startswith("a"):
            # show ansi 16-color table
            from evennia.utils import ansi
            ap = ansi.ANSI_PARSER
            # ansi colors
            # show all ansi color-related codes
            col1 = ["%s%s|n" % (code, code.replace("|", "||")) for code, _ in ap.ext_ansi_map[6:14]]
            col2 = ["%s%s|n" % (code, code.replace("|", "||")) for code, _ in ap.ext_ansi_map[14:22]]
            col3 = ["%s%s|n" % (code.replace("\\",""), code.replace("|", "||").replace("\\", "")) for code, _ in ap.ext_ansi_map[-8:]]
            col2.extend(["" for i in range(len(col1)-len(col2))])
            table = utils.format_table([col1, col2, col3])
            string = "ANSI colors:"
            for row in table:
                string += "\n " + " ".join(row)
            self.msg(string)
            self.msg("||X : black. ||/ : return, ||- : tab, ||_ : space, ||* : invert, ||u : underline")
            self.msg("To combine background and foreground, add background marker last, e.g. ||r||[B.")

        elif self.args.startswith("x"):
            # show xterm256 table
            table = [[], [], [], [], [], [], [], [], [], [], [], []]
            for ir in range(6):
                for ig in range(6):
                    for ib in range(6):
                        # foreground table
                        table[ir].append("|%i%i%i%s|n" % (ir, ig, ib, "||%i%i%i" % (ir, ig, ib)))
                        # background table
                        table[6+ir].append("|[%i%i%i|%i%i%i%s|n" % (ir, ig, ib,
                                                            5 - ir, 5 - ig, 5 - ib,
                                                        "||[%i%i%i" % (ir, ig, ib)))
            table = self.table_format(table)
            string = "Xterm256 colors (if not all hues show, your client might not report that it can handle xterm256):"
            for row in table:
                string += "\n" + "".join(row)
            self.msg(string)
            #self.msg("(e.g. %%123 and %%[123 also work)")
        else:
            # malformed input
            self.msg("Usage: @color ansi||xterm256")


class CmdQuell(MuxPlayerCommand):
    """
    use character's permissions instead of player's

    Usage:
      quell
      unquell

    Normally the permission level of the Player is used when puppeting a
    Character/Object to determine access. This command will switch the lock
    system to make use of the puppeted Object's permissions instead. This is
    useful mainly for testing.
    Hierarchical permission quelling only work downwards, thus a Player cannot
    use a higher-permission Character to escalate their permission level.
    Use the unquell command to revert back to normal operation.
    """

    key = "@quell"
    aliases = ["@unquell"]
    locks = "cmd:pperm(Players)"
    help_category = "General"

    def _recache_locks(self, player):
        "Helper method to reset the lockhandler on an already puppeted object"
        if self.session:
            char = self.session.puppet
            if char:
                # we are already puppeting an object. We need to reset
                # the lock caches (otherwise the superuser status change
                # won't be visible until repuppet)
                char.locks.reset()
        player.locks.reset()

    def func(self):
        "Perform the command"
        player = self.player
        permstr = player.is_superuser and " (superuser)" or " (%s)" % (", ".join(player.permissions.all()))
        if self.cmdstring == '@unquell':
            if not player.attributes.get('_quell'):
                self.msg("Already using normal Player permissions%s." % permstr)
            else:
                player.attributes.remove('_quell')
                self.msg("Player permissions%s restored." % permstr)
        else:
            if player.attributes.get('_quell'):
                self.msg("Already quelling Player%s permissions." % permstr)
                return
            player.attributes.add('_quell', True)
            puppet = self.session.puppet
            if puppet:
                cpermstr = " (%s)" % ", ".join(puppet.permissions.all())
                cpermstr = "Quelling to current puppet's permissions%s." % cpermstr
                cpermstr += "\n(Note: If this is higher than Player permissions%s, the lowest of the two will be used.)" % permstr
                cpermstr += "\nUse @unquell to return to normal permission usage."
                self.msg(cpermstr)
            else:
                self.msg("Quelling Player permissions%s. Use @unquell to get them back." % permstr)
        self._recache_locks(player)

