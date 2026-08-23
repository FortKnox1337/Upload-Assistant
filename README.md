> [!IMPORTANT]
> This repository is no longer being maintained.
>
> If you want to continue using the Python version of Upload Assistant, please move to **[wastaken7/Upload-Assistant](https://github.com/wastaken7/Upload-Assistant)**.
>
> If you’re interested in the newer successor project, take a look at **[autobrr/upbrr](https://github.com/autobrr/upbrr)**. Just bear in mind that upbrr is still in alpha.

## What Was This Fork? (A Little Backstory)

Development of the original Python Upload Assistant was put on hold while work moved towards upbrr. This also meant that various tracker additions, fixes, and other pull requests were left waiting.

I still wanted to use those updates, so I merged some of the pending work into my own personal fork. I also added several changes of my own, including NordicQuality support, the MidnightScene image host, configurable screenshot scaling, LostImg support, PeerGarden, and the TLZ refactor.

Other additions, such as the HUNO API fix and support for MidnightScene and RetroMoviesClub, came from pull requests created by their respective original authors. I only brought those changes into this fork temporarily while they were waiting during the development freeze.

I eventually shared the fork with a few other people who also wanted to keep using an updated version of Upload Assistant.

That was really all this was supposed to be: a temporary, personal fork that became useful to a few other people. It was never intended to become a separate project or an alternative version known as “FortKnox's UA.” 😅

## Why Is It Being Retired?

Development of the Python Upload Assistant project has now continued under wastaken7.

Most of the changes that made this fork useful have since been merged into, reimplemented in, or superseded by the maintained version. The changes I developed have also been contributed there so there isn’t much point in maintaining two versions containing much of the same work, and I don’t want people becoming confused about which repository they should be using.

Because of that, this fork is now retired and will not receive any further features, fixes, releases, or support.

## Where Should I Go Now?

### Upload Assistant

For the actively maintained Python version of Upload Assistant, use: **[github.com/wastaken7/Upload-Assistant](https://github.com/wastaken7/Upload-Assistant)**

> [!NOTE]
> Parts of the configuration and some tracker identifiers have changed—for example, abbreviations such as `HHD` now use the full identifier `HOMIEHELPDESK`.
>
> I recommend generating a fresh configuration using the maintained version, then manually transferring the settings and credentials you still need rather than copying your existing `config.py` unchanged.

Back up your existing configuration and cookies before migrating.

### upbrr

If you want to follow or try the newer successor to Upload Assistant, see: **[github.com/autobrr/upbrr](https://github.com/autobrr/upbrr)**

upbrr is a newer implementation of the Upload Assistant workflow and includes migration support for existing Upload Assistant configurations.

It is still alpha software, so read the documentation carefully and double-check everything it generates before submitting uploads.

## One Last Clarification

There isn’t really a separate project called “FortKnox's UA.” It is simply what this temporary fork came to be known as after I shared it.

I’m not claiming ownership of Upload Assistant or the work contributed by its many developers. Changes taken from pending pull requests remain the work of their original authors, while the changes I developed have been contributed back to the maintained project.

Thanks to Audionut, wastaken7, everyone who contributed the fixes and tracker implementations included here, and everyone continuing to work on Upload Assistant and upbrr.

Related projects:

- [Audionut/Upload-Assistant](https://github.com/Audionut/Upload-Assistant)
- [wastaken7/Upload-Assistant](https://github.com/wastaken7/Upload-Assistant)
- [autobrr/upbrr](https://github.com/autobrr/upbrr)
