package dev.maverick

import com.intellij.openapi.Disposable
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import java.net.URI
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import javax.swing.JButton
import javax.swing.JPanel
import javax.swing.JTextField
import javax.swing.SwingUtilities
import javax.swing.Timer

/** Optional IDE scaffold: a single owned reader, bounded output, and cursor-based replay. */
class RunsToolWindowFactory : ToolWindowFactory {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val output = JBTextArea().apply { isEditable = false }
        val goalField = JTextField(8)
        val watch = JButton("Watch live")
        val stop = JButton("Stop")
        val disposed = AtomicBoolean(false)
        val generation = AtomicLong()
        val cursors = ConcurrentHashMap<String, Long>()
        var active: RunEventStream? = null
        var pendingStart: (() -> Unit)? = null
        // Do not start a replacement until both the old reader and its closer have exited.
        val closing = Timer(100, null)
        closing.addActionListener {
            if (disposed.get()) { closing.stop(); return@addActionListener }
            if (active?.isAlive() != true) {
                closing.stop()
                active = null
                val next = pendingStart
                pendingStart = null
                next?.invoke()
            }
        }

        fun stopCurrent() {
            generation.incrementAndGet()
            pendingStart = null
            active?.close()
            if (active?.isAlive() == true) closing.start()
        }

        watch.addActionListener {
            val goalId = goalField.text.trim().toLongOrNull()?.takeIf { it > 0 } ?: return@addActionListener
            stopCurrent()
            val mine = generation.get()
            val base = (System.getenv("MAVERICK_DASHBOARD_URL") ?: "http://127.0.0.1:8765").trimEnd('/')
            val token = System.getenv("MAVERICK_DASHBOARD_TOKEN")
            val uri = try { URI("$base/api/v1/goals/$goalId/events/stream") } catch (_: Exception) { null }
            output.text = ""
            if (uri == null || !RunEventStream.validEndpoint(uri) ||
                (!token.isNullOrBlank() && !RunEventStream.tokenTransportSafe(uri))) {
                output.text = "[Use HTTP(S) without embedded credentials, and HTTPS or loopback for a bearer token.]\n"
                return@addActionListener
            }
            val key = MessageDigest.getInstance("SHA-256").digest((uri.toString() + "\u0000" + token.orEmpty()).toByteArray())
                .joinToString("") { "%02x".format(it) }
            if (!cursors.containsKey(key) && cursors.size >= 16) cursors.remove(cursors.keys.first())
            val start: () -> Unit = start@{
                if (disposed.get() || mine != generation.get()) return@start
                val cursor = cursors[key] ?: 0L
                if (cursor > 0) output.append("[Resuming after event $cursor; see the dashboard for earlier history.]\n")
                active = RunEventStream(uri, token, cursor, emit = { line ->
                    var accepted = false
                    // Synchronous UI handoff bounds queued work and advances cursor only after display.
                    SwingUtilities.invokeAndWait {
                        if (!disposed.get() && !project.isDisposed && mine == generation.get()) {
                            if (output.document.length + line.length > 1_000_000) {
                                output.text = "[Earlier output cleared; see the dashboard for full history.]\n"
                            }
                            output.append(line + "\n")
                            accepted = true
                        }
                    }
                    accepted
                }, saveCursor = { cursorValue ->
                    if (!disposed.get() && mine == generation.get()) cursors[key] = cursorValue
                }).also { it.start() }
            }
            if (active?.isAlive() == true) {
                output.append("[Closing the previous watch before opening this run.]\n")
                pendingStart = start
                closing.start()
            } else { active = null; start() }
        }
        stop.addActionListener { stopCurrent(); output.append("[Live watch stopped.]\n") }
        val controls = JPanel().apply { add(goalField); add(watch); add(stop) }
        val panel = JPanel(java.awt.BorderLayout()).apply {
            add(controls, java.awt.BorderLayout.NORTH)
            add(JBScrollPane(output), java.awt.BorderLayout.CENTER)
        }
        val content = toolWindow.contentManager.factory.createContent(panel, "", false)
        content.setDisposer(Disposable {
            disposed.set(true)
            stopCurrent()
            closing.stop()
            cursors.clear()
        })
        toolWindow.contentManager.addContent(content)
    }
}
