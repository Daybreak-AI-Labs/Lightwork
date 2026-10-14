import os, pathlib, shutil, subprocess, sys

repo = pathlib.Path(__file__).resolve().parent
task = repo.parent
tools = task / 'audit/native-tools'
jdk = tools / 'jdk-21.0.8+9'
env = dict(os.environ, JAVA_HOME=str(jdk), GRADLE_USER_HOME=str(tools / 'gradle-home'))
env['PATH'] = str(jdk / 'bin') + os.pathsep + env.get('PATH', '')
temporary = tools / 'gradle-tmp'
temporary.mkdir(exist_ok=True)
env['TEMP'] = env['TMP'] = str(temporary)
command = [str(jdk / 'bin/java.exe'), '-Djava.io.tmpdir=' + str(temporary), '-classpath', str(tools / 'gradle-8.13/lib/gradle-gradle-cli-main-8.13.jar'), 'org.gradle.launcher.GradleMain']
project = task / 'audit/jetbrains-build'
project.mkdir(parents=True, exist_ok=True)
for folder in ('src', 'tests'):
    shutil.copytree(task / 'Lightwork-staging/apps/jetbrains-plugin' / folder, project / folder, dirs_exist_ok=True)
for name in ('build.gradle.kts', 'settings.gradle.kts', 'gradle.properties', 'gradle.lockfile', 'buildscript-gradle.lockfile', 'build.py'):
    if not (repo / 'apps/jetbrains-plugin' / name).exists():
        continue
    shutil.copy2(repo / 'apps/jetbrains-plugin' / name, project / name)
settings = project / 'src/main/kotlin/dev/maverick/DashboardSettings.kt'
settings.write_bytes(settings.read_bytes().replace(b'field("Connection name", name)', b'field("Connection name", this@DashboardConfigurable.name)'))
print('Compiling current integrated JetBrains sources in task-local build directory', flush=True)
if sys.argv[1:] == ['package']:
    shutil.copytree(repo/'apps/jetbrains-plugin/gradle', project/'gradle', dirs_exist_ok=True)
    raise SystemExit(subprocess.call([sys.executable, 'build.py'], cwd=project, env=env))
raise SystemExit(subprocess.call(command + ['--no-daemon', '--console=plain', '--max-workers=2'] + sys.argv[1:], cwd=project, env=env))
