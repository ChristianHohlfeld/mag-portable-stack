# Contributing

MAG lives in this repository, `mag-portable-stack`. Do not add MAG room,
deploy, backup or bot-VM code to other repositories.

Branches are short-lived. Open a pull request, and delete the branch after it
merges (`delete_branch_on_merge` is on). `main` is the source. `green` is the
commit the server deploys; it moves only when `main` is green. Do not commit
to `green` by hand.

A merged branch whose history is not obvious from `main` gets an annotated
`archive/<name>` tag before the branch is deleted.

`mag-llm` and `mag-llm-iq3` stay running across application changes. Do not
restart them from a pull request, a deploy hook or a cleanup script.
