
import boto3
from pathlib import Path
import os
from subprocess import call
import tempfile
from ssm_cache import SSMParameterGroup, SSMParameter, InvalidParameterError


VALID_PARAM_TYPES = ["String", "SecureString", "StringList"]


# make sure we're using the same client as the SSMParameter objects
ssm = boto3.client('ssm')
SSMParameter.set_ssm_client(ssm)


def get_stages(project):
    project_path = (Path("/") / project).as_posix()
    group = SSMParameterGroup(base_path=project_path)
    param_paths = [Path(x.full_name) for x in group.parameters("/")]
    stage_names = set(p.parts[2] for p in param_paths)
    return [Stage(project, x) for x in stage_names]


def create_param_path(project, stage_name, param_name):
    return (Path("/") / project / stage_name / param_name).as_posix()


class ParameterNotFound(Exception):
    pass


class ParamSchemaValidationError(Exception):
    def __init__(self, message=None, errors=[]):
        super(ParamSchemaValidationError, self).__init__(message)
        self.errors = errors


class ParamCreateError(Exception):
    pass


class ParamDeleteError(Exception):
    pass


class Stage:

    def __init__(self, project, stage_name):
        self.project = project
        self.name = stage_name
        if self.name is None:
            raise ParamCreateError

    @property
    def project_path(self):
        return (Path("/") / self.project).as_posix()

    @property
    def path(self):
        return (Path("/") / self.project / self.name).as_posix()

    def param_path(self, param_name):
        return (Path("/") / self.project / self.name / param_name).as_posix()

    def get_params(self):
        group = SSMParameterGroup(base_path=self.path)
        for ssm_param in group.parameters("/"):
            yield Param(ssm_param)

    def validate(self, schema, filename=None):
        existing_params = set()
        if filename:
            with open(filename, "r") as f:
                for line in f.readlines():
                    existing_params.add(line.strip().split("=")[0])
        else:
            existing_params = set([x.name for x in self.get_params()])

        schema_params = set(schema.keys())
        errors = []
        for missing in existing_params.difference(schema_params):
            errors.append(
                "{} exists in parameter store but is not in the schema".format(missing)
            )
        for missing in schema_params.difference(existing_params):
            errors.append(
                "{} defined in schema but missing from parameter store".format(missing)
            )
        if len(errors):
            raise ParamSchemaValidationError(errors=errors)

    def delete_param(self, config, param_name):
        Param.delete(self.project, self.name, param_name)


class Param:

    def __init__(self, ssm_param):
        self.ssm_param = ssm_param
        self.path = Path(ssm_param.full_name)

    @classmethod
    def delete(cls, project, stage_name, param_name):
        param_path = create_param_path(project, stage_name, param_name)
        try:
            ssm.delete_parameter(Name=param_path)
            return param_path
        except ssm.exceptions.ClientError as e:
            raise ParamDeleteError(
                "Delete {} failed: {}".format(param_path, e)
            )

    @classmethod
    def create(cls, project, stage_name, param_name,
               param_value, param_type, param_desc=None,
               overwrite=False, tags={}):
        param_path = create_param_path(project, stage_name, param_name)
        if not param_value:
            return None

        if param_type not in VALID_PARAM_TYPES:
            raise ParamCreateError("Invalid parameter type: {}".format(param_type))

        try:
            tag_list = [
                {"Key": k, "Value": v}
                for k, v in tags.items()
            ]
            param_resp = ssm.put_parameter(
                Name=param_path,
                Description=param_desc,
                Value=param_value,
                Type=param_type,
                Overwrite=overwrite
            )
            if len(tag_list):
                tag_resp = ssm.add_tags_to_resource(
                    ResourceType="Parameter",
                    ResourceId=param_path,
                    Tags=tag_list
                )
        except ssm.exceptions.ClientError as e:
            raise ParamCreateError(str(e))

        ssm_param = SSMParameter(param_path)
        param = Param(ssm_param)
        if not param.exists():
            raise ParamCreateError("Something went wrong creating {}".format(param_path))
        return param

    def __getattr__(self, attr):
        return getattr(self.ssm_param, attr)

    def exists(self):
        try:
            self.refresh()
            return True
        except InvalidParameterError as e:
            return False

    @property
    def project(self):
        return self.path.parts[1]

    @property
    def stage(self):
        return self.path.parts[2]

    @property
    def name(self):
        return self.path.parts[-1]

    @property
    def envname(self):
        return self.name.upper().replace('-', '_')

    @property
    def dotenv(self):
        return "{}={}".format(self.envname, self.value)
