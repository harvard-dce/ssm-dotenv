
import toml
import json
import click
import os
import time
import tempfile
from subprocess import call
from os.path import join, dirname
from dotenv import load_dotenv
from pathlib import Path
from .params import get_stages, Stage, Param, ParamSchemaValidationError

env_file = join(dirname(__file__), '.env')
load_dotenv(env_file)

CONFIG_FILE = '.ssm-dotenv'


def config_path(config_file):
    return config_file and Path(config_file) or Path(CONFIG_FILE)


def get_config(config_file):
    path = config_path(config_file)
    if not path.exists():
        click.echo("ssm-dotenv config not found at {}".format(path))
        raise click.Abort()
    with open(path, 'r') as f:
        config = toml.load(f)
    return config


def switch_to(stage):
    with open(env_file, 'w') as f:
        f.write("CURRENT={}".format(stage))


def current_stage():
    name = os.getenv('CURRENT')
    if name == '':
        name = None
    return name


def getenv(name, required=True):
    config = get_config(CONFIG_FILE)
    stage = Stage(config["project"], current_stage())
    param = stage.get_param(name)

    if param is None:
        val = None
    else:
        val = param.value.strip('"').strip("'")

    if required and val is None:
        raise Exception("{} not defined".format(name))


@click.group()
@click.option('--config-file', help="path to alternative ssm-dotenv config file")
@click.pass_context
def cli(ctx, config_file):
    ctx.meta[CONFIG_FILE] = config_file
    ctx.obj = config_file
    if ctx.invoked_subcommand != 'config-example':
        if not os.path.exists(config_path(config_file)):
            click.echo("Config file {} doesn't exist.".format(config_file))
            raise click.Abort()

    if ctx.invoked_subcommand in ['edit', 'list-parameters', 'delete']:
        if not current_stage():
            click.echo("No current stage!")
            click.echo("Try `ssm-dotenv new` or `ssm-dotenv switch`")
            raise click.Abort()


@cli.command()
def config_example():
    click.echo('project=project-name\n'
               '\n'
               '[schema]\n'
               'PARAM_NAME0 = ["String", "description"]\n'
               'PARAM_NAME1 = ["SecureString", "description"]\n'
               'PARAM_NAME2 = ["StringList", "description"]\n'
               '\n'
               '[tags]\n'
               'ssm_dotenv="1"')


@cli.command()
@click.pass_obj
def show_config(config_file):
    click.echo(json.dumps(get_config(config_file), indent=2))


@cli.command()
@click.pass_obj
def switch(config_file):
    """
    Switch stages.
    """

    project = get_config(config_file)["project"]
    stage = __select_a_stage(project)
    click.echo("\nSwitching to {}".format(stage))
    switch_to(stage)


def __select_a_stage(project):
    stages = get_stages(project)

    if not stages:
        raise click.ClickException("No stages exist in the project '{}'!"
                                   " `ssm-dotenv new` to create a new stage")

    for num, stage_object in enumerate(stages):
        stage = stage_object.name
        click.echo("{} {} {}".format(num + 1, '*' if stage == current_stage() else ' ', stage))

    while True:
        num = input("\nStage number: ")

        if not num.isdigit() or int(num) == 0 or int(num) > len(stages):
            click.echo("\nEnter valid parameter number.")
        else:
            stage = stages[int(num) - 1].name
            break

    return stage


@cli.command()
@click.pass_obj
def list_parameters(config_file):
    """
    List parameters in current stage.
    """
    config = get_config(config_file)
    stage = Stage(config["project"], current_stage())

    click.echo("Current path: {}".format(stage.path))
    for param in stage.get_params():
        click.echo(param.dotenv)


@cli.command()
@click.pass_obj
def new(config_file):
    """
    Create a new stage.
    """
    config = get_config(config_file)

    existing_names = [s.name for s in get_stages(config["project"])]

    stage_name = click.prompt("Enter name of new stage")
    if not stage_name or stage_name in existing_names:
        click.echo("Stage named {} already exists".format(stage_name))
        raise click.Abort()
    switch_to(stage_name)

    if click.confirm("Populate parameters from existing stage?"):
        project = config["project"]
        base_stage_name = __select_a_stage(project)
        base_stage = Stage(project, base_stage_name)

    while current_stage() != stage_name:
        time.sleep(1)

    __edit_stage(config_file, copy_values_from=base_stage)


@cli.command()
@click.pass_obj
def edit(config_file):
    """
    Edit current stage.
    """
    __edit_stage(config_file)


@cli.command()
@click.pass_obj
def delete(config_file):
    """
    Delete current stage.
    """
    stage = Stage(get_config(config_file)["project"], current_stage())
    click.confirm("Are you sure you want to delete all parameters in path {}?".format(stage.path), abort=True)
    stage_name = click.prompt("Type name of stage to confirm")

    if stage_name != stage.name:
        raise click.Abort()

    for param in stage.get_params():
        click.echo("Deleting {}={}".format(param.path, param.dotenv))
        param.delete()

    switch_to('')


def __edit_stage(config_file, copy_values_from=None):

    stage = Stage(get_config(config_file)["project"], current_stage())
    tf = TemporaryFile(config_file, stage, copy_values_from)

    while True:
        try:
            tf.open_editor()
            changes = tf.diff()
            if changes:
                click.echo("\n".join(tf.diff()) + "\n")
                if click.confirm("Accept changes?"):
                    break
                if click.confirm("Continue editing?", abort=True):
                    continue
            if not changes:
                click.echo("No changes made")
                return
        except ParamSchemaValidationError as e:
            click.echo("\nSchema validation failed:")
            for error in e.errors:
                click.echo(error, err=True)
            click.confirm("Continue editing?", abort=True)

    tf.push_updates()
    tf.delete()


class TemporaryFile:

    def __init__(self, config_file, stage, base_stage=None):
        self.config_file = config_file
        self.stage = stage

        dotenv_content = []
        schema = get_config(config_file)["schema"]

        if base_stage:
            existing_params = {p.name: p for p in base_stage.get_params()}
        else:
            existing_params = {p.name: p for p in self.stage.get_params()}

        for field in schema:
            if field in existing_params:
                dotenv_content.append(existing_params[field].dotenv)
            else:
                dotenv_content.append("{}=".format(field))

        # write a temporary file with the current parameters
        f = tempfile.NamedTemporaryFile(mode='w', delete=False)
        f.write("\n".join(dotenv_content) + "\n")
        f.close()

        self.name = f.name
        self.envs = {}

    def open_editor(self):
        editor = os.environ.get("EDITOR")
        if not editor:
            editor = "vim"

        call([editor, self.name])

        self.validate()

        self.envs = {}
        with open(self.name, "r") as tf:
            lines = tf.readlines()
            for line in lines:
                env_name, env_value = line.strip().split("=")
                self.envs[env_name] = env_value

    def validate(self):
        schema = get_config(self.config_file)["schema"]
        self.stage.validate(schema, self.name)

    def diff(self):
        existing_params = {p.name: p.value for p in self.stage.get_params()}

        changes = []
        for param in self.envs:
            if param not in existing_params:
                if self.envs[param]:
                    changes.append("Adding param {}={}".format(param, self.envs[param]))
                else:
                    changes.append("Warning: param {} not defined".format(param))
            elif existing_params[param] != self.envs[param]:
                changes.append("Updating param {} from {} to {}"
                               .format(param, existing_params[param],
                                       self.envs[param]))

        for param in self.deleted_params():
            changes.append("Deleting param {}".format(param))

        return changes

    def deleted_params(self):
        deleted_params = {}
        existing_params = {p.name: p.value for p in self.stage.get_params()}

        for param in existing_params:
            if param not in self.envs:
                deleted_params[param] = existing_params[param]

        return deleted_params

    def push_updates(self):
        config = get_config(self.config_file)
        schema = config["schema"]
        tags = config.get("tags", {})

        for param in self.envs:
            if param not in schema:
                click.echo(
                    "Param {}(value={}) not in schema, skipping update"
                        .format(param, self.envs[param])
                )
                continue
            param_type = schema[param][0]
            param_desc = None
            if len(schema[param]) > 1:
                param_desc = schema[param][1]
            args = [param, self.envs[param], param_type, param_desc]

            Param.create(
                self.stage.project,
                self.stage.name,
                *args,
                overwrite=True,
                tags=tags
            )

        for param in self.deleted_params():
            self.stage.delete(param)

    def delete(self):
        os.unlink(self.name)


if __name__ == '__main__':
    cli()
