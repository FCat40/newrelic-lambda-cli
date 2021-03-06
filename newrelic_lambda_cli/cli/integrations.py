# -*- coding: utf-8 -*-

import boto3
import click

from newrelic_lambda_cli import api, integrations, permissions
from newrelic_lambda_cli.cli.decorators import add_options, AWS_OPTIONS, NR_OPTIONS
from newrelic_lambda_cli.cliutils import done, failure


@click.group(name="integrations")
def integrations_group():
    """Manage New Relic AWS Lambda Integrations"""
    pass


def register(group):
    group.add_command(integrations_group)
    integrations_group.add_command(install)
    integrations_group.add_command(uninstall)
    integrations_group.add_command(update)


@click.command(name="install")
@add_options(AWS_OPTIONS)
@click.option(
    "--aws-role-policy",
    help="Alternative AWS role policy to use for integration",
    metavar="<arn>",
)
@click.option(
    "--enable-logs",
    "-e",
    help="Determines if logs are forwarded to New Relic Logging",
    is_flag=True,
)
@click.option(
    "--memory-size",
    "-m",
    default=128,
    help="Memory size (in MiB) for the log ingestion function",
    metavar="<size>",
    show_default=True,
    type=click.INT,
)
@click.option(
    "--linked-account-name",
    "-n",
    help="New Relic Linked Account Label",
    metavar="<name>",
    required=False,
)
@add_options(NR_OPTIONS)
@click.option(
    "--timeout",
    "-t",
    default=30,
    help="Timeout (in seconds) for the New Relic log ingestion function",
    metavar="<secs>",
    show_default=True,
    type=click.INT,
)
@click.option(
    "--role-name",
    default=None,
    help="The name of a pre-created execution role for the log ingest function",
    metavar="<role_name>",
    show_default=False,
)
@click.option(
    "--enable-license-key-secret/--disable-license-key-secret",
    default=True,
    show_default=True,
    help="Enable/disable the license key managed secret",
)
@click.option(
    "--integration-arn",
    default=None,
    help="The ARN of a pre-existing AWS IAM role for the New Relic Lambda integration",
    metavar="<role_arn>",
    show_default=False,
)
@click.option(
    "--tag",
    "tags",
    default=[],
    help="A tag to be added to the CloudFormation Stack (can be used multiple times)",
    metavar="<key> <value>",
    multiple=True,
    nargs=2,
)
@click.pass_context
def install(
    ctx,
    aws_profile,
    aws_region,
    aws_permissions_check,
    aws_role_policy,
    enable_logs,
    memory_size,
    linked_account_name,
    nr_account_id,
    nr_api_key,
    nr_region,
    timeout,
    role_name,
    enable_license_key_secret,
    integration_arn,
    tags,
):
    """Install New Relic AWS Lambda Integration"""
    session = boto3.Session(profile_name=aws_profile, region_name=aws_region)

    if aws_permissions_check:
        permissions.ensure_integration_install_permissions(session)

    click.echo("Validating New Relic credentials")
    gql_client = api.validate_gql_credentials(nr_account_id, nr_api_key, nr_region)

    click.echo("Retrieving integration license key")
    nr_license_key = api.retrieve_license_key(gql_client)

    if not linked_account_name:
        linked_account_name = (
            "New Relic Lambda Integration - %s"
            % integrations.get_aws_account_id(session)
        )

    click.echo("Checking for a pre-existing link between New Relic and AWS")
    integrations.validate_linked_account(session, gql_client, linked_account_name)

    click.echo("Creating the AWS role for the New Relic AWS Lambda Integration")
    role = integrations.create_integration_role(
        session, aws_role_policy, nr_account_id, integration_arn, tags
    )

    if enable_license_key_secret:
        click.echo("Creating the managed secret for the New Relic License Key")
        integrations.install_license_key(session, nr_license_key, tags)

    install_success = True

    if role:
        click.echo("Linking New Relic account to AWS account")
        res = api.create_integration_account(
            gql_client, nr_account_id, linked_account_name, role
        )
        install_success = res and install_success

        click.echo("Enabling Lambda integration on the link between New Relic and AWS")
        res = api.enable_lambda_integration(
            gql_client, nr_account_id, linked_account_name
        )
        install_success = res and install_success

    click.echo("Creating newrelic-log-ingestion Lambda function in AWS account")
    res = integrations.install_log_ingestion(
        session, nr_license_key, enable_logs, memory_size, timeout, role_name, tags
    )
    install_success = res and install_success

    if install_success:
        done("Install Complete")

        if ctx.obj["VERBOSE"]:
            click.echo(
                "\nNext steps: Add the New Relic layers to your Lambda functions with "
                "the below command.\n"
            )
            command = [
                "$",
                "newrelic-lambda",
                "layers",
                "install",
                "--function",
                "all",
                "--nr-account-id",
                nr_account_id,
            ]
            if aws_profile:
                command.append("--aws-profile %s" % aws_profile)
            if aws_region:
                command.append("--aws-region %s" % aws_region)
            click.echo(" ".join(command))
    else:
        failure("Install Incomplete. See messages above for details.", exit=True)


@click.command(name="uninstall")
@add_options(AWS_OPTIONS)
@click.option(
    "--nr-account-id",
    "-a",
    envvar="NEW_RELIC_ACCOUNT_ID",
    help="New Relic Account ID",
    metavar="<id>",
    required=False,
    type=click.INT,
)
@click.option("--force", "-f", help="Force uninstall non-interactively", is_flag=True)
def uninstall(aws_profile, aws_region, aws_permissions_check, nr_account_id, force):
    """Uninstall New Relic AWS Lambda Integration"""
    session = boto3.Session(profile_name=aws_profile, region_name=aws_region)

    if aws_permissions_check:
        permissions.ensure_integration_uninstall_permissions(session)

    uninstall_integration = True

    if not force and nr_account_id:
        uninstall_integration = click.confirm(
            "This will uninstall the New Relic AWS Lambda integration role. "
            "Are you sure you want to proceed?"
        )

    if uninstall_integration and nr_account_id:
        integrations.remove_integration_role(session, nr_account_id)

    if not force:
        click.confirm(
            "This will uninstall the New Relic AWS Lambda log ingestion function and "
            "role. Are you sure you want to proceed?",
            abort=True,
            default=False,
        )

    integrations.remove_log_ingestion_function(session)

    if not force:
        click.confirm(
            "This will uninstall the New Relic License Key managed secret, and IAM "
            "Policy. "
            "Are you sure you want to proceed?",
            abort=True,
            default=False,
        )
    integrations.remove_license_key(session)

    done("Uninstall Complete")


@click.command(name="update")
@add_options(AWS_OPTIONS)
@click.option(
    "--enable-logs/--disable-logs",
    default=None,
    help="Determines if logs are forwarded to New Relic Logging",
)
@click.option(
    "--memory-size",
    "-m",
    help="Memory size (in MiB) for the log ingestion function",
    metavar="<size>",
    type=click.INT,
)
@click.option(
    "--timeout",
    "-t",
    help="Timeout (in seconds) for the New Relic log ingestion function",
    metavar="<secs>",
    type=click.INT,
)
@click.option(
    "--role-name",
    default=None,
    help="The name of a new pre-created execution role for the log ingest function",
    metavar="<role_name>",
    show_default=False,
)
@click.option(
    "--enable-license-key-secret/--disable-license-key-secret",
    default=True,
    show_default=True,
    help="Enable/disable the license key managed secret",
)
@click.option(
    "--tag",
    "tags",
    default=[],
    help="A tag to be added to the CloudFormation Stack (can be used multiple times)",
    metavar="<key> <value>",
    multiple=True,
    nargs=2,
)
def update(
    aws_profile,
    aws_region,
    aws_permissions_check,
    enable_logs,
    memory_size,
    timeout,
    role_name,
    enable_license_key_secret,
    tags,
):
    """UpdateNew Relic AWS Lambda Integration"""
    session = boto3.Session(profile_name=aws_profile, region_name=aws_region)

    if aws_permissions_check:
        permissions.ensure_integration_install_permissions(session)

    update_success = True

    click.echo("Updating newrelic-log-ingestion Lambda function in AWS account")
    res = integrations.update_log_ingestion(
        session, None, enable_logs, memory_size, timeout, role_name, tags
    )
    update_success = res and update_success

    if enable_license_key_secret:
        update_success = update_success and integrations.auto_install_license_key(
            session, tags
        )
    else:
        integrations.remove_license_key(session)

    if update_success:
        done("Update Complete")
    else:
        failure("Update Incomplete. See messages above for details.", exit=True)
