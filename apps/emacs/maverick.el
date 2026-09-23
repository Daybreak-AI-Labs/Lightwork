;;; maverick.el --- Drive the Lightwork agent runtime from Emacs -*- lexical-binding: t; -*-

;; Copyright (C) 2027 Day AI Labs

;; Author: Day AI Labs
;; Version: 0.1.0
;; Package-Requires: ((emacs "27.1"))
;; Keywords: tools, processes
;; URL: https://github.com/Daybreak-AI-Labs/Lightwork

;;; Commentary:

;; A thin, dependency-free front end over the `maverick' CLI:
;;
;;   M-x maverick-start    — prompt for a goal, run it (async, compilation buffer)
;;   M-x maverick-status   — show runtime status (+ cost)
;;   M-x maverick-monitor  — live plan-tree TUI in a term buffer
;;   M-x maverick-logs     — tail recent run logs
;;   M-x maverick-halt     — arm the killswitch (~/.maverick/HALT)
;;   M-x maverick-unhalt   — clear the killswitch
;;
;; The package shells out to the locally installed CLI; nothing here talks to
;; a network itself.  Set `maverick-cli-path' if `maverick' isn't on PATH.

;;; Code:

(require 'json)

(defgroup maverick nil
  "Drive the Lightwork agent runtime."
  :group 'tools
  :prefix "maverick-")

(defcustom maverick-cli-path "maverick"
  "Path to the maverick CLI executable."
  :type 'string
  :group 'maverick)

(defcustom maverick-default-max-dollars nil
  "When non-nil, pass --max-dollars to `maverick start'."
  :type '(choice (const :tag "Use config default" nil) number)
  :group 'maverick)

(defun maverick--command (&rest args)
  "Build a shell command string from `maverick-cli-path' and ARGS."
  (mapconcat #'shell-quote-argument
             (cons maverick-cli-path (delq nil args))
             " "))

;;;###autoload
(defun maverick-start (goal)
  "Start the Lightwork swarm on GOAL (asynchronously)."
  (interactive "sGoal for Lightwork: ")
  (let ((cmd (if maverick-default-max-dollars
                 (maverick--command "start" goal "--max-dollars"
                                    (number-to-string maverick-default-max-dollars))
               (maverick--command "start" goal))))
    (compilation-start cmd nil (lambda (_) "*maverick-run*"))))

;;;###autoload
(defun maverick-status ()
  "Show Lightwork runtime status, including cost."
  (interactive)
  (let ((buf (get-buffer-create "*maverick-status*")))
    (with-current-buffer buf
      (read-only-mode -1)
      (erase-buffer)
      (call-process-shell-command (maverick--command "status" "--cost") nil buf)
      (special-mode))
    (pop-to-buffer buf)))

;;;###autoload
(defun maverick-monitor ()
  "Open the live plan-tree monitor in a term buffer."
  (interactive)
  (require 'term)
  (let ((buf (term-ansi-make-term "*maverick-monitor*" maverick-cli-path nil "monitor")))
    (with-current-buffer buf (term-char-mode))
    (pop-to-buffer buf)))

;;;###autoload
(defun maverick-logs ()
  "Show recent Lightwork run logs."
  (interactive)
  (compilation-start (maverick--command "logs") nil (lambda (_) "*maverick-logs*")))

;;;###autoload
(defun maverick-halt ()
  "Arm the local Lightwork killswitch for upcoming execution checkpoints."
  (interactive)
  (when (yes-or-no-p "Arm the local killswitch? Running work stops at its next checkpoint. ")
    (maverick--checked-halt "halt" t)))

(defun maverick--checked-halt (command present)
  "Run COMMAND and validate its local halt-file state PRESENT."
  (condition-case err
      (with-temp-buffer
        ;; argv execution preserves literal arguments and does not interpret
        ;; a signalled process or unreadable response as successful mutation.
        (let ((status (call-process maverick-cli-path nil t nil command "--json")))
          (unless (and (integerp status) (zerop status))
            (error "Command not acknowledged (exit/signal %s)" status))
          (let* ((json-object-type 'alist)
                 (json-key-type 'symbol)
                 (json-false :false)
                 (result (json-read-from-string (buffer-string))))
            (unless (and (equal (alist-get 'command result) command)
                         (eq (alist-get 'acknowledged result) t)
                         (equal (alist-get 'scope result) "local")
                         (eq (alist-get 'halt_file_present result) (if present t :false))
                         (eq (alist-get 'work_stopped result) :false))
              (error "Local halt-file state was not confirmed"))
            (message "%s" (if present
                "Local kill switch armed. Running work stops at its next checkpoint."
              "Local kill switch cleared. Prior tasks were not restarted.")))))
    (error (message "Lightwork outcome unconfirmed: %s. Check local state before retrying."
                    (error-message-string err)))))

;;;###autoload
(defun maverick-unhalt ()
  "Clear the Lightwork killswitch."
  (interactive)
  (maverick--checked-halt "unhalt" nil))

(provide 'maverick)

;;; maverick.el ends here
