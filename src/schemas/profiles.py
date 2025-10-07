from datetime import date
from pathlib import Path

from fastapi import UploadFile, Form, File
from pydantic import BaseModel, field_validator, HttpUrl, ConfigDict

from validation import (
    validate_name,
    validate_image,
    validate_gender,
    validate_birth_date,
)

# Write your code here

class ProfileCreateRequestSchema(BaseModel):
    first_name: str
    last_name: str
    gender: str
    date_of_birth: date
    info: str
    avatar: UploadFile

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_names(cls, value: str) -> str:
        validate_name(value)
        return value.strip().lower()

    @field_validator("gender")
    @classmethod
    def validate_gender_value(cls, value: str) -> str:
        validate_gender(value)
        return value.lower()

    @field_validator("date_of_birth")
    @classmethod
    def validate_birth_date_value(cls, value: date) -> date:
        validate_birth_date(value)
        return value

    @field_validator("info")
    @classmethod
    def validate_info(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Info field cannot be empty or contain only spaces.")
        return value

    @field_validator("avatar")
    @classmethod
    def validate_avatar(cls, value: UploadFile) -> UploadFile:
        validate_image(value)
        return value

    @classmethod
    def as_form(
        cls,
        first_name: str = Form(...),
        last_name: str = Form(...),
        gender: str = Form(...),
        date_of_birth: date = Form(...),
        info: str = Form(...),
        avatar: UploadFile = File(...),
    ) -> "ProfileCreateRequestSchema":

        return cls(
            first_name=first_name,
            last_name=last_name,
            gender=gender,
            date_of_birth=date_of_birth,
            info=info,
            avatar=avatar,
        )

    def avatar_extension(self) -> str:

        filename = self.avatar.filename or ""
        suffix = Path(filename).suffix.lower()
        return suffix if suffix else ".jpg"


class ProfileResponseSchema(BaseModel):

    id: int
    user_id: int
    first_name: str | None
    last_name: str | None
    gender: str | None
    date_of_birth: date | None
    info: str | None
    avatar: HttpUrl | None

    model_config = ConfigDict(from_attributes=True)
