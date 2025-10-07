import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import aiosmtplib
from jinja2 import Environment, FileSystemLoader

from exceptions import BaseEmailError
from notifications.interfaces import EmailSenderInterface
from notifications.mailhog import store_mailhog_message


class EmailSender(EmailSenderInterface):
    def __init__(
        self,
        hostname: str,
        port: int,
        email: str,
        password: str,
        use_tls: bool,
        template_dir: str,
        activation_email_template_name: str,
        activation_complete_email_template_name: str,
        password_email_template_name: str,
        password_complete_email_template_name: str,
        mailhog_api_port: int | None = None,
    ):
        self._hostname = hostname
        self._port = port
        self._email = email
        self._password = password
        self._use_tls = use_tls
        self.mailhog_api_port = mailhog_api_port
        self._activation_email_template_name = activation_email_template_name
        self._activation_complete_email_template_name = activation_complete_email_template_name
        self._password_email_template_name = password_email_template_name
        self._password_complete_email_template_name = password_complete_email_template_name
        self._env = Environment(loader=FileSystemLoader(template_dir))

    async def _send_email(self, recipient: str, subject: str, html_content: str) -> None:
        message = MIMEMultipart()
        message["From"] = self._email
        message["To"] = recipient
        message["Subject"] = subject
        message.attach(MIMEText(html_content, "html"))

        if any(h in self._hostname for h in ("127.0.0.1", "localhost", "mailhog_theater")):
            host = "127.0.0.1"
            api_port = self.mailhog_api_port or 8025
            await store_mailhog_message(host, api_port, recipient, subject, html_content)
            return

        try:
            smtp = aiosmtplib.SMTP(hostname=self._hostname, port=self._port, start_tls=self._use_tls)
            await smtp.connect()
            if self._use_tls:
                await smtp.starttls()
            await smtp.login(self._email, self._password)
            await smtp.sendmail(self._email, [recipient], message.as_string())
            await smtp.quit()
        except aiosmtplib.SMTPException as error:
            logging.error(f"Failed to send email to {recipient}: {error}")
            raise BaseEmailError(f"Failed to send email to {recipient}: {error}")

    async def send_activation_email(self, email: str, activation_link: str) -> None:
        template = self._env.get_template(self._activation_email_template_name)
        html_content = template.render(email=email, activation_link=activation_link)
        await self._send_email(email, "Account Activation", html_content)

    async def send_activation_complete_email(self, email: str, login_link: str) -> None:
        template = self._env.get_template(self._activation_complete_email_template_name)
        html_content = template.render(email=email, login_link=login_link)
        await self._send_email(email, "Account Activated Successfully", html_content)

    async def send_password_reset_email(self, email: str, reset_link: str) -> None:
        template = self._env.get_template(self._password_email_template_name)
        html_content = template.render(email=email, reset_link=reset_link)
        await self._send_email(email, "Password Reset Request", html_content)

    async def send_password_reset_complete_email(self, email: str, login_link: str) -> None:
        template = self._env.get_template(self._password_complete_email_template_name)
        html_content = template.render(email=email, login_link=login_link)
        await self._send_email(email, "Your Password Has Been Successfully Reset", html_content)
