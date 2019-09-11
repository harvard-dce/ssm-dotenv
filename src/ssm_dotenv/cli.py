
import toml
import json
import click
import os
import tempfile
from subprocess import call
from os.path import join, dirname
from dotenv import load_dotenv
from pathlib import Path
from .params import get_stages, Stage, Param, TemporaryFile, \
    ParamSchemaValidationError, ParamCreateError, ParamDeleteError

env_file = join(dirname(__file__), '.env')
load_dotenv(env_file)

CONFIG_FILE = '.ssm-dotenv'


def get_config(config_file):
    config_path = config_file and Path(config_file) or Path(CONFIG_FILE)
    if not config_path.exists():
        click.echo("ssm-dotenv config not found at {}".format(config_path))
        raise click.Abort()
    with open(config_path, 'r') as f:
        config = toml.load(f)
    ctx = click.get_current_context()
    ctx.meta["CONFIG_PATH"] = config_path
    return config


def update_config(config):
    ctx = click.get_current_context()
    config_path = ctx.meta["CONFIG_PATH"]
    with open(config_path, 'w') as f:
        toml.dump(config, f)


def switch_to(stage):
    with open(env_file, 'w') as f:
        f.write("CURRENT={}".format(stage))


def current_stage():
    name = os.getenv('CURRENT')
    if name == '':
        name = None
    return name

# def push(config, input):
#     create_args = []
#     for line in input:
#         param_name, param_value = line.strip().split("=")
#         if param_name not in config["schema"]:
#             click.echo("{} not found in schema".format(param_name))
#             raise click.Abort()
#         param_type = config["schema"][param_name]
#         create_args.append([param_name, param_value, param_type])
#     for argset in create_args:
#         try:
#             param = Param.create(
#                 config["project"],
#                 current_stage(),
#                 *argset,
#                 overwrite=True,
#                 tags=config.get("tags", {})
#             )
#             click.echo("Created {}".format(param.full_name))
#         except ParamCreateError as e:
#             click.echo(e, err=True)
#             raise click.Abort()


@click.group()
@click.option('--config-file', help="path to alternative ssm-dotenv config file")
@click.pass_context
def cli(ctx, config_file):
    ctx.obj = get_config(config_file)


@cli.command()
@click.pass_obj
def show_config(config):
    click.echo(json.dumps(config, indent=2))


@cli.command()
@click.argument('param-name')
@click.argument('param-value')
@click.argument('param-type', required=False)
@click.pass_obj
def add(config, param_name, param_value, param_type):
    if param_type is None:
        if param_name in config["schema"]:
            param_type = config["schema"][param_name]
        else:
            click.echo("Unknown param type for {}".format(param_name), err=True)
            raise click.Abort()
    try:
        param = Param.create(
            config["project"],
            current_stage(),
            param_name,
            param_value,
            param_type,
            overwrite=True,
            tags=config.get("tags", {})
        )
        click.echo("Created {}".format(param.full_name))
        if param_name not in config["schema"] or \
                config["schema"][param_name] != param_type:
            config["schema"][param_name] = param_type
            update_config(config)
            click.echo("Schema updated")
    except ParamCreateError as e:
        click.echo(e, err=True)
        raise click.Abort()


@cli.command()
@click.pass_obj
def validate(config):
    stage = Stage(config["project"], current_stage())
    try:
        stage.validate(config["schema"])
        click.echo("All good!")
    except ParamSchemaValidationError as e:
        click.echo("Schema validation errors:\n{}" \
                   .format("\n".join(e.errors)))


@cli.command()
@click.pass_obj
def switch(config):
    """
    Switch stages.
    """

    stages = get_stages(config["project"])

    if not stages:
        raise click.ClickException("No stages exist in the project '{}'!"
                                   " `ssm-dotenv new` to create a new stage")

    for num, stage_object in enumerate(stages):
        stage = stage_object.name
        click.echo("{} {} {}".format(num + 1, '*' if stage == current_stage() else ' ', stage))

    num = input("\nStage number: ")

    if not num.isdigit() or int(num) == 0 or int(num) > len(stages):
        click.echo("\nEnter valid parameter number.")
    else:ZZ
        stage = stages[int(num) - 1].name
        click.echo("\nSwitching to {}".format(stage))
        switch_to(stage)


@cli.command()
@click.pass_obj
def list_parameters(config):
    stage = Stage(config["project"], current_stage())

    click.echo("\nCurrent path: {}".format(stage.path))
    for param in stage.get_params():
        click.echo(param.dotenv)


@cli.command()
@click.pass_obj
def edit(config):
    stage = Stage(config["project"], current_stage())
    tf = TemporaryFile(stage)

    while True:
        try:
            tf.open_editor(config["schema"])
            changes = tf.diff()
            if changes:
                click.echo("\n".join(tf.diff()) + "\n")
                click.echo("Accept changes? [yn] ", nl=False)
                c = click.getchar()
                if c == 'y':
                    break
            if not changes:
                click.echo("No changes made")
                return
        except ParamSchemaValidationError as e:
            for error in e.errors:
                click.echo(error, err=True)
            click.echo('Continue editing? [yn] ', nl=False)
            c = click.getchar()
            click.echo()
            if c != 'y':
                tf.delete()
                raise click.Abort()
            else:
                continue

    tf.push_updates(config["schema"], config.get("tags", {}))
    tf.delete()


if __name__ == '__main__':
    cli()
