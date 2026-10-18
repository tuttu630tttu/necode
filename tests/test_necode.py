"""Integration checks; run with python3 -m unittest discover -s tests."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'necode.sh'


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='necode-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project with spaces'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.source = self.root / 'nested folder' / 'test.cpp'
        self.source.parent.mkdir()
        self.env = dict(os.environ, TMPDIR=self.temp.name)

    def run_source(self, code, args=(), data=''):
        self.source.write_text(code)
        result = subprocess.run(
            [str(SCRIPT), str(self.source), *args], cwd=self.temp.name,
            input=data, text=True, capture_output=True, env=self.env, timeout=30,
        )
        self.assertEqual(list(Path(self.temp.name).glob('necode.*')), [])
        return result

    def test_success_does_not_retry(self):
        result = self.run_source('''#include <iostream>
int main(){int n;std::cin>>n;std::cout<<n+1<<"\\n";
std::cerr<<"diagnostic\\n";}''', data='41\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '42\n')
        self.assertRegex(result.stderr, r'^\[stderr\]  diagnostic\n\n\[time\] real [0-9.]+ s \| user [0-9.]+ s \| sys [0-9.]+ s\n$')
        self.assertTrue((self.root / 'out/nested folder/test').is_file())

    def test_failure_replays_complete_input_and_preserves_failure(self):
        self.set_clipboard('20 22\n')
        result = self.run_source('''#include <iostream>
int main(){int a;std::cin>>a;
#ifdef _GLIBCXX_DEBUG
std::cout<<"checked "<<a<<"\\n";return 7;
#else
int b;std::cin>>b;std::cout<<"fallback "<<a+b<<"\\n";
#endif
}''', args=['--paste'])
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, 'checked 20\nfallback 42\n')
        self.assertIn('検査なしの終了コード: 0', result.stderr)

    def set_clipboard(self, data):
        clipboard = Path(self.temp.name) / 'pbpaste'
        count = Path(self.temp.name) / 'clipboard-count'
        content = Path(self.temp.name) / 'clipboard-content'
        content.write_bytes(data.encode())
        clipboard.write_text('#!/bin/bash\nprintf x >> "$CLIPBOARD_COUNT"\n'
                             'cat "$CLIPBOARD_CONTENT"\n')
        clipboard.chmod(0o755)
        self.env.update(PATH=self.temp.name + os.pathsep + os.environ['PATH'],
                        CLIPBOARD_COUNT=str(count), CLIPBOARD_CONTENT=str(content))
        return count

    def test_clipboard_change_during_compile_does_not_change_either_run(self):
        import shutil
        count = self.set_clipboard('42\n')
        compiler = next((shutil.which(name) for name in
                         ['g++-16', 'g++-15', 'g++-14', 'g++', 'clang++']
                         if shutil.which(name)), None)
        self.assertIsNotNone(compiler)
        wrapper = Path(self.temp.name) / 'compiler'
        # Simulate copying another input as soon as compilation starts.
        wrapper.write_text('#!/bin/bash\nprintf "99\\n" > "$CLIPBOARD_CONTENT"\n'
                           'exec "$REAL_CXX" "$@"\n')
        wrapper.chmod(0o755)
        self.env.update(CXX=str(wrapper), REAL_CXX=compiler)
        result = self.run_source('#include <iostream>\nint main(){int n;std::cin>>n;'
                                 'std::cout<<n<<"\\n";'
                                 '\n#ifdef _GLIBCXX_DEBUG\nreturn 7;\n#endif\n}',
                                 args=['--paste'])
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, '42\n42\n')
        self.assertEqual(count.read_text(), 'x')
        self.assertEqual(Path(self.env['CLIPBOARD_CONTENT']).read_text(), '99\n')

    def test_paste_compile_failure_cleans_up_snapshot(self):
        count = self.set_clipboard('42\n')
        result = self.run_source('invalid C++', args=['--paste'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Compile Error', result.stderr)
        self.assertNotIn('[time]', result.stderr)
        self.assertEqual(count.read_text(), 'x')

    def test_container_error_retries_clipboard_once(self):
        count = self.set_clipboard('\033[200~42\033[201~\n')
        result = self.run_source('''#include <vector>
#include <iostream>
int main(){int n;std::cin>>n;std::vector<int> v(2,n);v.pop_back();
std::cout<<v[1]<<"\\n";}''', args=['--paste'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('検査なしの実行結果', result.stderr)
        self.assertIn('検査なしの終了コード:', result.stderr)
        self.assertEqual(count.read_text(), 'x')

    def test_compile_failure_does_not_retry(self):
        result = self.run_source('invalid C++')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Compile Error', result.stderr)
        self.assertNotIn('[time]', result.stderr)
        self.assertNotIn('検査なし', result.stderr)

    def test_warning_does_not_retry(self):
        result = self.run_source('int main(){int unused;}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('warning:', result.stderr)
        self.assertNotIn('検査なし', result.stderr)

    def test_interrupt_does_not_retry(self):
        self.set_clipboard('')
        result = self.run_source('#include <csignal>\nint main(){raise(SIGINT);}', args=['--paste'])
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertNotIn('検査なし', result.stderr)

    def test_failed_fallback_reports_both_exit_codes(self):
        self.set_clipboard('')
        result = self.run_source('''int main(){
#ifdef _GLIBCXX_DEBUG
return 7;
#else
return 9;
#endif
}''', args=['--paste'])
        self.assertEqual(result.returncode, 7)
        self.assertIn('検査なしの終了コード: 9', result.stderr)

    def test_manual_failure_does_not_retry(self):
        result = self.run_source('int main(){return 7;}')
        self.assertEqual(result.returncode, 7)
        self.assertNotIn('検査なし', result.stderr)

    def test_manual_mode_runs_before_stdin_closes(self):
        self.source.write_text('#include <iostream>\nint main(){std::cout<<"ready\\n"<<std::flush;}')
        process = subprocess.Popen([str(SCRIPT), str(self.source)],
                                   cwd=self.temp.name, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=self.env)
        try:
            # stdin remains open throughout: a buffering implementation hangs here.
            self.assertEqual(process.wait(timeout=15), 0)
            self.assertEqual(process.stdout.read(), 'ready\n')
            self.assertRegex(process.stderr.read(), r'^\n\[time\] real [0-9.]+ s \| user [0-9.]+ s \| sys [0-9.]+ s\n$')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_multiline_stderr_and_missing_final_newline(self):
        result = self.run_source('#include <iostream>\nint main(){std::cout<<"answer";'
                                 'std::cerr<<"one\\ntwo";return 3;}')
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, 'answer')
        self.assertTrue(result.stderr.startswith('[stderr]  one\n[stderr]  two\n\n[time] '), result.stderr)
        self.assertNotIn('\033', result.stderr)

    def test_fallback_stderr_is_also_formatted(self):
        self.set_clipboard('')
        result = self.run_source('#include <iostream>\nint main(){std::cerr<<"trace\\n";'
                                 '\n#ifdef _GLIBCXX_DEBUG\nreturn 7;\n#endif\n}',
                                 args=['--paste'])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr.count('[stderr]  trace'), 2)
        self.assertEqual(result.stderr.count('[time] real '), 2)

    def run_in_terminal(self, code):
        import pty
        import select
        self.source.write_text(code)
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen([str(SCRIPT), str(self.source)],
                                       cwd=self.temp.name, stdin=subprocess.DEVNULL,
                                       stdout=slave, stderr=slave, env=self.env)
            try:
                self.assertEqual(process.wait(timeout=15), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            chunks = []
            while select.select([master], [], [], 0.2)[0]:
                chunk = os.read(master, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
            return b''.join(chunks).decode()
        finally:
            os.close(master)
            os.close(slave)

    def test_terminal_color_and_separator_after_stdout_without_newline(self):
        self.env['TERM'] = 'xterm-256color'
        self.env.pop('NO_COLOR', None)
        output = self.run_in_terminal('#include <iostream>\nint main(){std::cout<<"answer";'
                                      'std::cerr<<"trace\\n";}')
        self.assertIn('answer\r\n\033[33m[stderr]\033[0m  trace\r\n', output)

    def test_log_tags_and_color_names_in_terminal(self):
        self.env['TERM'] = 'xterm-256color'
        self.env.pop('NO_COLOR', None)
        shades = {'trace': 90, 'debug': 35, 'info': 36, 'warn': 33,
                  'error': 31, 'success': 32, 'red': 31, 'green': 32,
                  'yellow': 33, 'blue': 34, 'magenta': 35, 'cyan': 36,
                  'white': 37, 'gray': 90, 'ERROR': 31}
        lines = ''.join(f'[{tag}] value\n' for tag in shades)
        code = '#include <iostream>\nint main(){std::cerr<<' + json.dumps(lines) + ';}'
        output = self.run_in_terminal(code)
        for tag, shade in shades.items():
            self.assertIn(f'\033[{shade}m[{tag}]\033[0m{" " * (8 - len(tag))}value\r\n', output)
        self.assertNotIn('[stderr]', output)

    def test_tags_remain_readable_without_terminal_colors(self):
        result = self.run_source('#include <iostream>\nint main(){std::cout<<"answer";'
                                 'std::cerr<<"[info] hello\\n[red] world\\n'
                                 '[custom] untouched\\nvalue [error] middle\\n";}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'answer')
        self.assertTrue(result.stderr.startswith('[info]    hello\n[red]     world\n'
                        '[stderr]  [custom] untouched\n[stderr]  value [error] middle\n'), result.stderr)
        self.assertNotIn('\033', result.stderr)

    def test_no_color_and_dumb_terminal_preserve_tags(self):
        for settings in ({'TERM': 'xterm', 'NO_COLOR': '1'}, {'TERM': 'dumb'}):
            with self.subTest(settings=settings):
                self.env.pop('NO_COLOR', None)
                self.env.update(settings)
                output = self.run_in_terminal('#include <iostream>\nint main(){std::cerr<<"[warn] look\\n";}')
                self.assertIn('[warn]    look\r\n', output)
                self.assertNotIn('\033', output)

    def test_retry_keeps_tagged_logs(self):
        self.set_clipboard('')
        result = self.run_source('#include <iostream>\nint main(){std::cerr<<"[debug] value\\n";'
                                 '\n#ifdef _GLIBCXX_DEBUG\nreturn 7;\n#endif\n}', args=['--paste'])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr.count('[debug]   value'), 2)
        self.assertNotIn('[stderr] [debug]', result.stderr)

    def test_execution_time_excludes_compilation_and_keeps_stdout_clean(self):
        import shutil
        compiler = next(shutil.which(name) for name in
                        ['g++-16', 'g++-15', 'g++-14', 'g++', 'clang++']
                        if shutil.which(name))
        wrapper = Path(self.temp.name) / 'slow-compiler'
        wrapper.write_text('#!/bin/bash\nsleep 1\nexec "$REAL_CXX" "$@"\n')
        wrapper.chmod(0o755)
        self.env.update(CXX=str(wrapper), REAL_CXX=compiler, TIMEFORMAT='ignore me')
        result = self.run_source('#include <thread>\n#include <chrono>\n#include <iostream>\n'
                                 'int main(){std::this_thread::sleep_for(std::chrono::milliseconds(150));'
                                 'std::cout<<"done";}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'done')
        match = re.search(r'\[time\] real ([0-9.]+) s \| user ([0-9.]+) s \| sys ([0-9.]+) s', result.stderr)
        self.assertIsNotNone(match, result.stderr)
        self.assertGreaterEqual(float(match[1]), 0.12)
        self.assertLess(float(match[1]), 1.0)
        self.assertNotIn('[stderr] [time]', result.stderr)

    def test_tag_columns_align_and_preserve_message_spacing(self):
        result = self.run_source('#include <iostream>\nint main(){std::cerr<<'
                                 '"[debug]  1\\n[red]  22\\n[success]  333\\nplain\\n";}')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stderr.splitlines()[:4]
        self.assertEqual(lines, ['[debug]    1', '[red]      22',
                                 '[success]  333', '[stderr]  plain'])
        self.assertEqual([line.index('  ') for line in lines[:3]], [7, 5, 9])

    def test_keybindings_keep_only_two_modes(self):
        bindings = json.loads((SCRIPT.parent / 'examples/keybindings.json').read_text())
        self.assertEqual([b['key'] for b in bindings], ['ctrl+;', 'ctrl+l'])
        self.assertNotIn('--no-debug', json.dumps(bindings))


if __name__ == '__main__':
    unittest.main()
