
import toml
import json
import click
from pathlib import Path
from .params import get_stages, Stage, Param, \
    ParamSchemaValidationError, ParamCreateError, ParamDeleteError

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
            config["stage"],
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
@click.argument('param-name')
@click.pass_obj
def delete(config, param_name):
    try:
        param_path = Param.delete(config["project"], config["stage"], param_name)
        click.echo("Deleted {}".format(param_path))
        if param_name in config["schema"]:
            del config["schema"][param_name]
            update_config(config)
            click.echo("Schema updated")
    except ParamDeleteError as e:
        click.echo(e, err=True)
        raise click.Abort()


@cli.command()
@click.pass_obj
def validate(config):
    stage = Stage(config["project"], config["stage"])
    try:
        stage.validate(config["schema"])
        click.echo("All good!")
    except ParamSchemaValidationError as e:
        click.echo("Schema validation errors:\n{}" \
                   .format("\n".join(e.errors)))

@cli.command()
@click.pass_obj
def list_stages(config):
    for stage in get_stages(config["project"]):
        click.echo(stage.name)


@cli.command()
@click.argument('output', type=click.File('w'))
@click.option('--strict/--no-strict', is_flag=True, default=True)
@click.pass_obj
def pull(config, output, strict):
    stage = Stage(config["project"], config["stage"])
    if strict:
        try:
            stage.validate(config["schema"])
        except ParamSchemaValidationError as e:
            click.echo("Schema validation errors:\n{}" \
                       .format("\n".join(e.errors)))
            raise click.Abort()
    dotenv_content = []
    for param in stage.get_params():
        dotenv_content.append(param.dotenv)
    output.write("\n".join(dotenv_content) + "\n")


@cli.command()
@click.argument('input', type=click.File('r'))
@click.pass_obj
def push(config, input):
    create_args = []
    for line in input:
        param_name, param_value = line.strip().split("=")
        if param_name not in config["schema"]:
            click.echo("{} not found in schema".format(param_name))
            raise click.Abort()
        param_type = config["schema"][param_name]
        create_args.append([param_name, param_value, param_type])
    for argset in create_args:
        try:
            param = Param.create(
                config["project"],
                config["stage"],
                *argset,
                overwrite=True,
                tags=config.get("tags", {})
            )
            click.echo("Created {}".format(param.full_name))
        except ParamCreateError as e:
            click.echo(e, err=True)
            raise click.Abort()


if __name__ == '__main__':
    cli()
