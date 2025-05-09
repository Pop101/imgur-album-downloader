#!/usr/bin/env python3
# encoding: utf-8


"""
imguralbum.py - Download a whole imgur album in one go.

Provides both a class and a command line utility in a single script
to download Imgur albums.

MIT License
Copyright Alex Gisby <alex@solution10.com>
"""


import sys
import re
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from requests.exceptions import ConnectionError, Timeout, HTTPError, RequestException, TooManyRedirects
from requests.packages.urllib3.exceptions import MaxRetryError

import os
from collections import Counter
from PIL import Image, UnidentifiedImageError
from io import BytesIO


help_message = """
Quickly and easily download an album from Imgur.

Format:
    $ python imguralbum.py [album URL] [destination folder]

Example:
    $ python imguralbum.py http://imgur.com/a/uOOju#6 /Users/alex/images

If you omit the dest folder name, the utility will create one with the same name
as the album
(for example for http://imgur.com/a/uOOju it'll create uOOju/ in the cwd)
"""


class ImgurAlbumException(Exception):
    def __init__(self, msg=False):
        self.msg = msg


class ImgurAlbumDownloader:
    def __init__(self, album_url:str , extn:list = None, retry_strategy:Retry = None, verbose:bool = False):
        """
        Will download an Imgur album given by the URL on construction. URL will be checked for validity.
        
        :param album_url: The URL of the Imgur album to download.
        :param extn: A list of file extensions to look for. Defaults to All
        :param retry_strategy: A Retry strategy to use for the requests. Defaults to 4 retries with exponential backoff.
        """
        self.album_url = album_url
        self.verbose = verbose

        if not retry_strategy:
            # Default retry strategy, with 4 retries and exponential backoff
            retry_strategy = Retry(
                total=4,
                backoff_factor=1, # 1, 2, 4, 8, 16 seconds between retries
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"]
            )
        
        # Callback members:
        self.image_callbacks = []
        self.success_callbacks = []
        self.complete_callbacks = []

        # Check the URL is actually imgur:
        match = re.match(r"(https?)\:\/\/(www\.)?(?:m\.)?imgur\.com/(?:(?:a|gallery)/)?([a-zA-Z0-9]+)(#[0-9]+)?", album_url)
        if not match:
            raise ImgurAlbumException("URL must be a valid Imgur Album {}".format(album_url))

        self.protocol = match.group(1)
        self.album_key = match.group(3)

        # Read the no-script version of the page for all the images:
        fullListURL = "http://imgur.com/a/" + self.album_key + "/layout/blog"


        # Create a requests session with the retry strategy
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
        self.session.mount("http://", HTTPAdapter(max_retries=retry_strategy))
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'image/*,text/html;q=0.8,*/*;q=0.7',
            'Referer': 'https://imgur.com/'
        }
        
        # Use the session to get the image with retries, redirects and headers
        try:
            self.response = self.session.get(fullListURL, headers=self.headers, allow_redirects=True, timeout=30)
            response_code = self.response.status_code
        except Exception as e:
            self.response = False
            response_code = e.code

        if not self.response or self.response.status_code != requests.codes['ok']:
            raise ImgurAlbumException("Error reading Imgur: Error Code %d" % response_code)

        # Read in the images now so we can get stats and stuff:
        html = self.response.text
        ext_regex = r"(\.(" + '|'.join(extn) + "))" if extn else ".*?"
        self.imageIDs = re.findall(r'.*?{"hash":"([a-zA-Z0-9]+)".*?"ext":"(\.(' + ext_regex + '))".*?', html)
        
        ## this is likely to have a lot of duplicates, so let's kill those
        self.imageIDs = list(set([i[0:2] for i in self.imageIDs]))
        self.imageURLs = ["https://i.imgur.com/" + i[0] + i[1] for i in self.imageIDs]
        
        
        self.cnt = Counter()
        
        for i in self.imageIDs:
            self.cnt[i[1]] += 1


    def num_images(self):
        """
        Returns the number of images that are present in this album.
        """
        return len(self.imageIDs)


    def list_extensions(self):
        """
        Returns list with occurrences of extensions in descending order.
        """  
        return self.cnt.most_common()


    def album_key(self):
        """
        Returns the key of this album. Helpful if you plan on generating your own
        folder names.
        """
        return self.album_key


    def on_image_download(self, callback):
        """
        Allows you to bind a function that will be called just before an image is
        about to be downloaded. You'll be given the 1-indexed position of the image, it's URL
        and it's destination file in the callback like so:
            my_awesome_callback(1, "http://i.imgur.com/fGWX0.jpg", "~/Downloads/1-fGWX0.jpg")
        """
        self.image_callbacks.append(callback)

    def on_download_success(self, callback):
        """
        Allows you to bind a function that will be called after an image is downloaded sucessfully.
        You'll be given the 1-indexed position of the image, it's URL
        and it's destination file in the callback like so:
            my_awesome_callback(1, "http://i.imgur.com/fGWX0.jpg", "~/Downloads/1-fGWX0.jpg")
        """
        self.success_callbacks.append(callback)

    def on_complete(self, callback):
        """
        Allows you to bind onto the end of the process, displaying any lovely messages
        to your users, or carrying on with the rest of the program. Whichever.
        """
        self.complete_callbacks.append(callback)


    def save_images(self, foldername = None, useKey = False):
        """
        Saves the images from the album into a folder given by foldername.
        If no foldername is given, it'll use the cwd and the album key.
        And if the folder doesn't exist, it'll try and create it.
        
        If addKey is true then the name of the image will be YYYYYY_XX
        where XX is the image number and YYYYY is the album key (which is
        a 'unique' Imgur created hash
        """
        # Try and create the album folder:
        if foldername != None:
            albumFolder = foldername
        else:
            albumFolder = self.album_key

        if not os.path.exists(albumFolder):
            os.makedirs(albumFolder)

        # And finally loop through and save the images:
        for (counter, image) in enumerate(self.imageIDs, start=1):
            image_url = "http://i.imgur.com/"+image[0]+image[1]

            suffix = "_{:0>2}".format(counter) ## should be good for up to 100 images
            path = ""
            if useKey:
                path = os.path.join(albumFolder, self.album_key + suffix + image[1])
            else:
                path = os.path.join(albumFolder, image[0] + suffix + image[1])

            # Run the callbacks:
            for fn in self.image_callbacks:
                fn(counter, image_url, path)

            # Actually download the thing
            if os.path.isfile(path):
                if self.verbose: print (f"Skipping, {path} alreadyexists.")
            else:
                try:
                    imageRequest = self.session.get(image_url, headers=self.headers, allow_redirects=True, timeout=30)
                    imageData = imageRequest.content
                    
                    im = Image.open(BytesIO(imageData))
                    w, h = im.size
                    im.close()
                    
                    if (w == 161 and h == 81): # this is the imgur image not found jpg
                        continue
                    
                    with open(path, 'wb') as fobj:
                        fobj.write(imageData)
                    
                    for fn in self.success_callbacks:
                        fn(counter, image_url, path)    
                    
                except (ConnectionError, Timeout, HTTPError, RequestException, TooManyRedirects, UnidentifiedImageError, MaxRetryError) as e:
                    if self.verbose: print (f"Download failed: {type(e).__name__} {e}")
                    if os.path.exists(path): os.remove(path)

        # Run the complete callbacks:
        for fn in self.complete_callbacks:
            fn()



if __name__ == '__main__':
    args = sys.argv

    if len(args) == 1:
        # Print out the help message and exit:
        print (help_message)
        exit()

    try:
        # Fire up the class:
        downloader = ImgurAlbumDownloader(args[1], verbose=True)

        print(("Found {0} images in album".format(downloader.num_images())))

        for i in downloader.list_extensions():
            print(("Found {0} files with {1} extension".format(i[1],i[0])))
  
        # Called when an image is about to download:
        def print_image_progress(index, url, dest):
            print(("Downloading Image %d" % index))
            print(("    %s >> %s" % (url, dest)))
        downloader.on_image_download(print_image_progress)

        # Called when the downloads are all done.
        def all_done():
            print ("")
            print ("Done!")
        downloader.on_complete(all_done)

        # Work out if we have a foldername or not:
        if len(args) == 3:
            albumFolder = args[2]
        else:
            albumFolder = None

        # Enough talk, let's save!
        downloader.save_images(albumFolder)
        exit()

    except ImgurAlbumException as e:
        print(("Error: " + e.msg))
        print ("")
        print ("How to use")
        print ("=============")
        print (help_message)
        exit(1)
