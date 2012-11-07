import sublime
import sublime_plugin
import os
import os.path
import operator
import itertools
import json
import functools
import threading

import minify_json

MODIFIERS = ('shift', 'ctrl', 'alt', 'super')


# A shameless copy of Will Bond marvelous code in https://github.com/wbond/sublime_package_control
class ThreadProgress(object):
    def __init__(self, thread, message, success_message):
        self.thread = thread
        self.message = message
        self.success_message = success_message
        self.addend = 1
        self.size = 8
        sublime.set_timeout(lambda: self.run(0), 500)

    def run(self, i):
        if not self.thread.is_alive():
            if hasattr(self.thread, 'result') and not self.thread.result:
                sublime.status_message('')
                return
            sublime.status_message(self.success_message)
            return

        before = i % self.size
        after = (self.size - 1) - before
        sublime.status_message('%s [%s=%s]' % \
            (self.message, ' ' * before, ' ' * after))
        if not after:
            self.addend = -1
        if not before:
            self.addend = 1
        i += self.addend
        sublime.set_timeout(lambda: self.run(i), 500)


class ParserThread(threading.Thread):
    def __init__(self, on_done, **kwargs):
        super(ParserThread, self).__init__(**kwargs)
        self.on_done = on_done

    def run(self):
        pluginspath = sublime.packages_path()
        allkeybindings = []
        for root, dirs, files in os.walk(pluginspath):
            for fpath in (os.path.join(root, f) for f in files if f.lower() in ('default (%s).sublime-keymap' % (sublime.platform(),), 'default.sublime-keymap')):
                with open(fpath) as f:
                    content = f.read()
                keybindings = json.loads(minify_json.json_minify(content))
                for e in keybindings:
                    # add package name
                    e['package'] = os.path.split(os.path.split(fpath)[0])[1]
                    # order context list to be able to compare it
                    if 'context' in e:
                        e['context'].sort()
                    else:
                        e['context'] = None
                    # normalize command string
                    for i, command in enumerate(e['keys']):
                        cmod, ckey = [], []
                        comp = command.split('+')
                        for c in comp:
                            if c in MODIFIERS:
                                cmod.append(c)
                            else:
                                ckey.append(c)
                            cmod.sort()
                        e['keys'][i] = '+'.join(cmod + ckey)
                allkeybindings.extend(keybindings)
        sublime.set_timeout(functools.partial(self.on_done, allkeybindings), 0)


class KeymapsCommand(object):
    def run(self, output='buffer'):
        self.output = output
        thread = ParserThread(self.thread_done)
        thread.start()
        ThreadProgress(thread, "Generating Keymaps Report", 'Generating Keymaps Report: Finished')

    def thread_done(self, result):
        # filter ignored packages
        ignoredpackages = [i.lower() for i in self.window.active_view().settings().get('ignored_packages')]
        result = [kb for kb in result if kb['package'].lower() not in ignoredpackages]

        result = self.generate_report(result)
        if self.output == 'buffer':
            self.report_to_buffer(result)
        else:
            self.report_to_quickpanel(result)

    @classmethod
    def generate_report(self, keybindings):
        pass

    def report_to_buffer(self, result):
        txt = ''
        for header, items in result:
            txt += '%s\n%s\n%s\n' % ('-' * len(header), header, '-' * len(header))
            for k, l in items:
                print k
                txt += ' [%s]\n' % (', '.join('"%s"' % (i,) for i in k), )
                for e in l:
                    txt += '   %-50s %-30s  %s\n' % (e['command'], e['package'], e['context'] if e['context'] else '')  # , e.get('context'))
        # create scratch panel for output
        panel = sublime.active_window().new_file()
        panel.set_scratch(True)
        panel.settings().set('word_wrap', False)
        # content output
        panel_edit = panel.begin_edit()
        panel.insert(panel_edit, 0, txt)
        panel.end_edit(panel_edit)

    def report_to_quickpanel(self, result):
        output = []
        for k, l in result:
            for e in l:
                output.append(['%-30s\t\t%s' % ('%s' % (', '.join('%s' % (i,) for i in e['keys']), ), e['command']), e['package']])  # , e.get('context'))
        sublime.active_window().show_quick_panel(output, None, sublime.MONOSPACE_FONT)


class AllKeymapsCommand(KeymapsCommand, sublime_plugin.WindowCommand):
    @classmethod
    def generate_report(cls, keybindings):
        # sorting function
        keysort = operator.itemgetter('keys', 'package', 'command', 'context')
        # order keybindings by keys
        keybindings.sort(key=keysort)
        result = []
        # all keybindings
        for k, g in itertools.groupby(keybindings, key=operator.itemgetter('keys')):
            result.append((k, list(g)))
        return [("All Keymaps", result)]


class ConflictKeymapsCommand(KeymapsCommand, sublime_plugin.WindowCommand):
    @classmethod
    def generate_report(cls, keybindings):
        # sorting function
        keysort = operator.itemgetter('keys', 'context', 'package', 'command')
        # order keybindings by keys
        keybindings.sort(key=keysort)
        result = []
        # group keybindings by keys and print only group > 1 (duplicate)
        for k, g in itertools.groupby(keybindings, key=operator.itemgetter('keys', 'context')):
            l = list(g)
            if len(l) > 1:
                result.append((k[0], l))
        return [("Keymaps redeclared", result)]


class AllConflictKeymapsCommand(KeymapsCommand, sublime_plugin.WindowCommand):
    @classmethod
    def generate_report(cls, keybindings):
        # first call conflict keymap
        result = ConflictKeymapsCommand.generate_report(keybindings)
        # sorting function
        keysort = operator.itemgetter('keys', 'context', 'package', 'command')
        # order keybindings by keys
        keybindings.sort(key=keysort)
        # Multi part keybindings that start with an existing single part keybinding
        conflict = []
        for k, g in itertools.groupby(keybindings, key=lambda x: [[x['keys'][0]], x['context']]):
            l = list(g)
            if len(l) > 1:
                singlekeys = len(l[0]['keys']) == 1
                multikeys = False
                for i in l[1:]:
                    if len(i['keys']) > 1:
                        multikeys = True

                if not (singlekeys and multikeys):
                    continue
                conflict.append((k[0], l))
        result.append(("Multi part Keymaps that start with an existing single part Keymap", conflict))
        return result
